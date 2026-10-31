"""Bound local monitoring request resources and incomplete reads."""
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

READ_TIMEOUT = 5
MAX_CLIENTS = 16


class MonitoringHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_CLIENTS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            super().handle_error(request, client_address)


class MonitoringHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(READ_TIMEOUT)
        self._read_lock = threading.Lock()
        self._reading = True
        self._expired = False
        self._read_expires = time.monotonic() + READ_TIMEOUT
        self._read_deadline = threading.Timer(READ_TIMEOUT, self._expire_read)
        self._read_deadline.daemon = True
        self._read_deadline.start()

    def _expire_read(self):
        with self._read_lock:
            if self._reading:
                self._expired = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def finish_reading(self):
        with self._read_lock:
            if self._expired or time.monotonic() >= self._read_expires:
                raise TimeoutError('request read deadline exceeded')
            self._reading = False
        self._read_deadline.cancel()

    def finish(self):
        with self._read_lock:
            self._reading = False
        self._read_deadline.cancel()
        super().finish()

    def log_message(self, *args):
        pass
