import hashlib
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from workflows.marketing.assets import verify_asset, render_decision, ingest_verified_asset
from runner.artifacts import ArtifactStore


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
    def test_invalid_compressed_image_with_valid_chunk_crc_is_blocked(self):
        data=png(); start=data.index(b'IDAT')+4; end=data.index(b'IEND')-4
        payload=b'x'*(end-start-4)
        corrupt=data[:start]+payload+struct.pack('>I',zlib.crc32(b'IDAT'+payload))+data[end:]
        self.asset.write_bytes(corrupt)
        self.run['asset_sha256']=hashlib.sha256(corrupt).hexdigest()
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', self.brief, self.run, 'project-v1')['status'])
    def test_video_without_probe_is_blocked(self):
        self.asset.write_bytes(b'not video')
        self.run['asset_sha256'] = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        brief = dict(self.brief, kind='mp4', duration_seconds=2)
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', brief, self.run, 'project-v1', probe='/nonexistent/ffprobe')['status'])
    def test_missing_decoder_blocks_even_a_valid_image(self):
        self.assertEqual('blocked', verify_asset(self.root, 'frame.png', self.brief, self.run, 'project-v1', decoder='/nonexistent/ffmpeg')['status'])
    def test_real_synthetic_video_is_decoded_and_version_bound(self):
        video=self.root/'video.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x48:r=5:d=1','-c:v','mpeg4',str(video)],check=True,timeout=20)
        run={**self.run,'asset_sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
        brief={**self.brief,'kind':'mp4','width':32,'height':48,'duration_seconds':1}
        result=verify_asset(self.root,'video.mp4',brief,run,'project-v1')
        self.assertEqual(result['status'],'awaiting_review')
        self.assertEqual((result['width'],result['height']),(32,48))
        self.assertEqual(result['audio_streams'],[])
        self.assertEqual(verify_asset(self.root,'video.mp4',brief,run,'stale')['status'],'blocked')
        self.assertEqual(verify_asset(self.root,'video.mp4',brief,run,'project-v1',probe='/nonexistent/ffprobe')['status'],'blocked')
    def test_video_audio_streams_are_decoded_and_reported(self):
        video=self.root/'audio-video.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x48:r=5:d=1','-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=1','-c:v','mpeg4','-c:a','aac','-shortest',str(video)],check=True,timeout=20)
        run={**self.run,'asset_sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
        brief={**self.brief,'kind':'mp4','width':32,'height':48,'duration_seconds':1}
        result=verify_asset(self.root,'audio-video.mp4',brief,run,'project-v1')
        self.assertEqual(result['status'],'awaiting_review')
        self.assertEqual(result['audio_streams'],[{'codec':'aac','sample_rate':44100,'channels':1}])
    def test_ingested_asset_binds_verified_bytes_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as storage:
            store=ArtifactStore(storage)
            result=ingest_verified_asset(store,self.root,'frame.png',self.brief,self.run,'project-v1')
            self.assertEqual(result['status'],'awaiting_review')
            self.assertEqual(store.get(result['artifact_ref']),self.asset.read_bytes())
            self.asset.write_bytes(png(1,1))
            self.assertEqual(ingest_verified_asset(store,self.root,'frame.png',self.brief,self.run,'project-v1')['status'],'blocked')
            (self.root/'alias.png').symlink_to(self.asset)
            self.assertEqual(ingest_verified_asset(store,self.root,'alias.png',self.brief,self.run,'project-v1')['status'],'blocked')
            self.assertEqual(ingest_verified_asset(store,self.root,'../outside.png',self.brief,self.run,'project-v1')['status'],'blocked')
    def test_retries_cancel_and_stale_version(self):
        self.assertEqual('run', render_decision(0, 2, False, 'v1', 'v1'))
        self.assertEqual('blocked', render_decision(2, 2, False, 'v1', 'v1'))
        self.assertEqual('cancelled', render_decision(0, 2, True, 'v1', 'v1'))
        self.assertEqual('stale', render_decision(0, 2, False, 'v1', 'v2'))

if __name__ == '__main__': unittest.main()
