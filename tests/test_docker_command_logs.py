import socketserver
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from runner.docker_controller import UnixEngine, DockerError

class DockerCommandLogsTests(unittest.TestCase):
    def test_bounded_multiplexed_logs_and_malformed_frames(self):
        class Handler(socketserver.StreamRequestHandler):
            payload=b''
            def handle(self):
                self.rfile.readline()
                while self.rfile.readline()!=b'\r\n': pass
                self.wfile.write(('HTTP/1.1 200 OK\r\nContent-Length: '+str(len(self.payload))+'\r\n\r\n').encode()+self.payload); self.wfile.flush()
        class Server(socketserver.ThreadingUnixStreamServer): daemon_threads=True
        frame=lambda channel,data:struct.pack('>BxxxI',channel,len(data))+data
        with tempfile.TemporaryDirectory() as root:
            path=str(Path(root)/'engine.sock'); server=Server(path,Handler); thread=threading.Thread(target=server.serve_forever); thread.start()
            try:
                engine=UnixEngine(path); Handler.payload=frame(1,b'hello')+frame(2,b'warning')+frame(1,b'!')
                self.assertEqual(engine.logs('a'*64,20),(b'hello!',b'warning'))
                with self.assertRaises(DockerError): engine.logs('a'*64,3)
                Handler.payload=b'\x01\x00\x00\x00\x00\x00\x00\x05bad'
                with self.assertRaises(DockerError): engine.logs('a'*64,20)
            finally: server.shutdown(); thread.join(); server.server_close()
