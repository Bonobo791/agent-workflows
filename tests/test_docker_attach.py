import json
import socket
import struct
import time
import unittest
from runner.docker_attach import DockerAttachTransport
from runner.stdio import StdioError

class DockerAttachTests(unittest.TestCase):
    def test_multiplexed_stdout_and_stderr(self):
        left,right=socket.socketpair()
        try:
            transport=DockerAttachTransport(left,time.monotonic()+2,lambda:False)
            transport.send({'method':'initialize'})
            self.assertEqual(json.loads(right.recv(4096)),{'method':'initialize'})
            def frame(channel,data): return struct.pack('>BxxxI',channel,len(data))+data
            right.sendall(frame(2,b'private stderr')+frame(1,b'{"id":')+frame(1,b'1}\n'))
            self.assertEqual(transport.receive(),{'id':1})
        finally: left.close(); right.close()
    def test_invalid_multiplex_header_fails_closed(self):
        left,right=socket.socketpair()
        try:
            transport=DockerAttachTransport(left,time.monotonic()+2,lambda:False)
            right.sendall(b'\x03\x00\x00\x00\x00\x00\x00\x00')
            with self.assertRaises(StdioError): transport.receive()
        finally: left.close(); right.close()

    def test_engine_upgrade_preserves_coalesced_first_frame(self):
        import os, socketserver, tempfile, threading
        from runner.docker_controller import UnixEngine
        cid='a'*64; requests=[]
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                data=b''
                while b'\r\n\r\n' not in data: data+=self.request.recv(1024)
                requests.append(data)
                frame=struct.pack('>BxxxI',1,9)+b'{"id":1}\n'
                self.request.sendall(b'HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream\r\n\r\n'+frame)
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'engine.sock'); server=Server(path,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                sock=UnixEngine(path).attach(cid)
                try: self.assertEqual(DockerAttachTransport(sock,time.monotonic()+2,lambda:False).receive(),{'id':1})
                finally: sock.close()
            finally: server.shutdown(); thread.join(); server.server_close()
        self.assertIn(('/v1.49/containers/'+cid+'/attach?stream=1&stdin=1&stdout=1&stderr=1').encode(),requests[0])

    def test_engine_rejects_non_upgrade_response(self):
        import os, socketserver, tempfile, threading
        from runner.docker_controller import UnixEngine, DockerError
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(4096); self.request.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
        class Server(socketserver.UnixStreamServer): pass
        with tempfile.TemporaryDirectory() as root:
            path=os.path.join(root,'engine.sock'); server=Server(path,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                with self.assertRaises(DockerError): UnixEngine(path).attach('a'*64)
            finally: server.shutdown(); thread.join(); server.server_close()

    def test_disconnect_during_partial_frame_is_error(self):
        left,right=socket.socketpair()
        try:
            transport=DockerAttachTransport(left,time.monotonic()+2,lambda:False)
            right.sendall(struct.pack('>BxxxI',1,20)+b'{'); right.close()
            with self.assertRaises(StdioError): transport.receive()
        finally: left.close(); right.close()

    def test_cancel_interrupts_wait(self):
        left,right=socket.socketpair()
        try:
            transport=DockerAttachTransport(left,time.monotonic()+2,lambda:True)
            with self.assertRaisesRegex(StdioError,'cancelled'): transport.receive()
        finally: left.close(); right.close()
