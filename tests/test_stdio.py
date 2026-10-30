import io
import unittest
from runner.stdio import NDJSONTransport, StdioError

class StdioTests(unittest.TestCase):
    def test_json_lines_and_invalid_input(self):
        reader=io.BytesIO(b'{"id":1,"result":{}}\ninvalid\n'); writer=io.BytesIO(); transport=NDJSONTransport(reader,writer)
        transport.send({'id':1,'method':'initialize'})
        self.assertEqual(transport.receive()['id'],1)
        self.assertTrue(writer.getvalue().endswith(b'\n'))
        with self.assertRaises(StdioError): transport.receive()
