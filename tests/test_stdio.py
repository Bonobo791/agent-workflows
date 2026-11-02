import io
import unittest
from runner.stdio import NDJSONTransport, StdioError

class StdioTests(unittest.TestCase):
    def test_large_source_prompt_is_accepted_but_transport_stays_bounded(self):
        from runner.stdio import MAX_SEND_LINE
        writer=io.BytesIO();transport=NDJSONTransport(io.BytesIO(),writer)
        transport.send({'prompt':'x'*(3*1024*1024)})
        self.assertLessEqual(len(writer.getvalue()),MAX_SEND_LINE)
        with self.assertRaises(StdioError):transport.send({'prompt':'x'*MAX_SEND_LINE})
    def test_process_transport_sends_large_prompt_without_raising_receive_limit(self):
        import subprocess,sys,time
        from runner.process_transport import ProcessTransport
        from runner.stdio import MAX_LINE
        child=subprocess.Popen([sys.executable,'-c',"import sys,json; m=json.loads(sys.stdin.readline()); print(json.dumps({'size':len(m['prompt'])}),flush=True)"],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
        try:
            transport=ProcessTransport(child,time.monotonic()+10,lambda:False)
            transport.send({'prompt':'x'*(3*1024*1024)})
            self.assertEqual(transport.receive(),{'size':3*1024*1024});self.assertEqual(MAX_LINE,2*1024*1024)
            self.assertEqual(child.wait(timeout=5),0)
        finally:
            if child.poll() is None:child.kill();child.wait()
            child.stdin.close();child.stdout.close()
    def test_incoming_frames_remain_limited(self):
        from runner.stdio import MAX_LINE
        transport=NDJSONTransport(io.BytesIO(b'{"v":"'+b'x'*MAX_LINE+b'"}\n'),io.BytesIO())
        with self.assertRaises(StdioError):transport.receive()
    def test_json_lines_and_invalid_input(self):
        reader=io.BytesIO(b'{"id":1,"result":{}}\ninvalid\n'); writer=io.BytesIO(); transport=NDJSONTransport(reader,writer)
        transport.send({'id':1,'method':'initialize'})
        self.assertEqual(transport.receive()['id'],1)
        self.assertTrue(writer.getvalue().endswith(b'\n'))
        with self.assertRaises(StdioError): transport.receive()
