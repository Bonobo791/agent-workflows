"""Bound local monitoring connections so unauthenticated peers cannot monopolize them."""
from http.server import HTTPServer
import socket
from socketserver import ThreadingMixIn
import sys
import threading

MAX_CONNECTIONS=4
REQUEST_TIMEOUT=10


def _expire_request(request):
    try:
        request.shutdown(socket.SHUT_RDWR)
    except OSError:
        # The peer may already have disconnected.
        pass


class LoopbackHTTPServer(ThreadingMixIn,HTTPServer):
    daemon_threads=True

    def __init__(self,port,handler):
        self.slots=threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(('127.0.0.1',port),handler)

    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self,request,client_address):
        timer=threading.Timer(REQUEST_TIMEOUT,_expire_request,args=(request,))
        timer.daemon=True
        try:
            timer.start()
            super().process_request_thread(request,client_address)
        finally:
            timer.cancel()
            self.shutdown_request(request)
            self.slots.release()

    def handle_error(self,request,client_address):
        if not isinstance(sys.exception(),OSError):
            super().handle_error(request,client_address)
