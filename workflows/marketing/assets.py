"""Independent technical checks; editorial and rights review remain human gates."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

MAX_BYTES = 100 * 1024 * 1024


def render_decision(attempts, max_attempts, cancelled, run_version, current_version):
    if cancelled:
        return 'cancelled'
    if not run_version or run_version != current_version:
        return 'stale'
    if type(attempts) is not int or type(max_attempts) is not int or attempts < 0 or max_attempts < 1 or attempts >= max_attempts:
        return 'blocked'
    return 'run'


def _png_dimensions(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('invalid PNG signature')
    pos, dimensions, ended, image_data = 8, None, False, False
    while pos + 12 <= len(data):
        length = struct.unpack_from('>I', data, pos)[0]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError('truncated PNG')
        kind = data[pos+4:pos+8]
        payload = data[pos+8:pos+8+length]
        expected = struct.unpack_from('>I', data, pos+8+length)[0]
        if zlib.crc32(kind + payload) != expected:
            raise ValueError('PNG checksum mismatch')
        if pos == 8 and (kind != b'IHDR' or length != 13):
            raise ValueError('invalid PNG header')
        if kind == b'IHDR':
            if dimensions is not None or length != 13:
                raise ValueError('invalid PNG header')
            dimensions = struct.unpack_from('>II', payload)
        if kind == b'IDAT':
            image_data = True
        if kind == b'IEND':
            if length != 0 or end != len(data):
                raise ValueError('invalid PNG end')
            ended = True
            break
        pos = end
    if not dimensions or not all(dimensions) or not image_data or not ended:
        raise ValueError('incomplete PNG')
    return dimensions


def verify_asset(root, relative_path, brief, run, current_project_version, probe='ffprobe'):
    """Check confined bytes and technical metadata; never certify content or rights."""
    try:
        if not brief.get('version') or run.get('brief_version') != brief['version']:
            raise ValueError('stale brief')
        if not current_project_version or run.get('project_version') != current_project_version:
            raise ValueError('stale project')
        base = Path(root).resolve(strict=True)
        path = (base / relative_path).resolve(strict=True)
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('asset outside private storage')
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('asset too large')
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not run.get('asset_sha256') or digest != run['asset_sha256']:
            raise ValueError('output changed')
        kind = brief.get('kind')
        if kind == 'png':
            width, height = _png_dimensions(data)
            metadata = {'width': width, 'height': height}
        elif kind == 'mp4':
            proc = subprocess.run([probe, '-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration,format_name', '-of', 'json', str(path)], capture_output=True, text=True, timeout=20, check=True)
            info = json.loads(proc.stdout)
            if 'mp4' not in info['format']['format_name'].split(','):
                raise ValueError('not MP4')
            stream = next(s for s in info['streams'] if s.get('codec_type') == 'video')
            duration = float(info['format']['duration'])
            if not 0 < duration < float('inf') or abs(duration - float(brief['duration_seconds'])) > 0.25:
                raise ValueError('duration mismatch')
            metadata = {'width': stream['width'], 'height': stream['height'], 'duration_seconds': duration}
        else:
            raise ValueError('unsupported asset type')
        if metadata['width'] != brief.get('width') or metadata['height'] != brief.get('height'):
            raise ValueError('dimensions mismatch')
        return {'status': 'awaiting_review', 'sha256': digest, **metadata,
                'reason': 'Technical checks passed; content, visual quality and rights require independent review.'}
    except (OSError, ValueError, TypeError, KeyError, StopIteration, struct.error, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {'status': 'blocked', 'reason': str(exc)}
