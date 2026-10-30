import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from workflows.marketing.assets import verify_asset, render_decision


def png(width=2, height=3):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    raw = b''.join(b'\0' + b'\xff\x00\x00' * width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')

class AssetVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.asset = self.root / 'frame.png'
        self.asset.write_bytes(png())
        self.digest = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.brief = {'version':'brief-v1', 'width':2, 'height':3, 'kind':'png'}
        self.run = {'brief_version':'brief-v1', 'project_version':'project-v1', 'asset_sha256':self.digest}
    def tearDown(self): self.temp.cleanup()

    def test_valid_fixture_requires_independent_content_review(self):
        result = verify_asset(self.root, 'frame.png', self.brief, self.run, 'project-v1')
        self.assertEqual('awaiting_review', result['status'])
        self.assertEqual((2,3), (result['width'], result['height']))
    def test_stale_or_changed_output_blocks(self):
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', self.brief, self.run, 'project-v2')['status'])
        self.asset.write_bytes(png(1,1))
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', self.brief, self.run, 'project-v1')['status'])
    def test_corrupt_png_and_path_escape_block(self):
        self.asset.write_bytes(png()[:-8])
        self.run['asset_sha256'] = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', self.brief, self.run, 'project-v1')['status'])
        self.assertEqual('blocked', verify_asset(self.root, '../outside.png', self.brief, self.run, 'project-v1')['status'])
    def test_video_without_probe_is_blocked(self):
        self.asset.write_bytes(b'not video')
        self.run['asset_sha256'] = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        brief = dict(self.brief, kind='mp4', duration_seconds=2)
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', brief, self.run, 'project-v1', probe='/nonexistent/ffprobe')['status'])
    def test_retries_cancel_and_stale_version(self):
        self.assertEqual('run', render_decision(0, 2, False, 'v1', 'v1'))
        self.assertEqual('blocked', render_decision(2, 2, False, 'v1', 'v1'))
        self.assertEqual('cancelled', render_decision(0, 2, True, 'v1', 'v1'))
        self.assertEqual('stale', render_decision(0, 2, False, 'v1', 'v2'))

if __name__ == '__main__': unittest.main()
