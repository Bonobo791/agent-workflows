"""One line per pipeline step on stderr, so a live run can be diagnosed from a single paste.

WORKFLOW_TRACE=0 turns it off. Hashes are shortened to 12 characters, lists to 8 items, text to 240 characters.
Tracing never raises: a logging failure must not change a pipeline outcome.
"""
import os
import re
import sys
import threading
import time

ENABLED = os.environ.get('WORKFLOW_TRACE', '1') != '0'
_lock = threading.Lock()
_HASH = re.compile(r'[0-9a-f]{64}')


def _show(value, depth=0):
    if value is None or isinstance(value, bool): return str(value)
    if isinstance(value, (int, float)): return str(value)
    if isinstance(value, bytes): return f'<{len(value)}B>'
    if isinstance(value, str):
        text = _HASH.sub(lambda match: match.group(0)[:12], value).replace('\n', '\\n')
        return text if len(text) <= 240 else f'{text[:240]}...(+{len(text) - 240})'
    if depth > 2: return '…'
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        shown = ','.join(_show(item, depth + 1) for item in items[:8])
        return f'[{shown}{f",+{len(items) - 8}" if len(items) > 8 else ""}]'
    if isinstance(value, dict):
        shown = ','.join(f'{key}:{_show(item, depth + 1)}' for key, item in list(value.items())[:8])
        return f'{{{shown}}}'
    return repr(value)[:240]


def trace(step, **fields):
    if not ENABLED: return
    try:
        line = ' '.join([time.strftime('%H:%M:%S'), step] + [f'{key}={_show(item)}' for key, item in fields.items()])
        with _lock:
            print(f'[trace] {line}', file=sys.stderr, flush=True)
    except Exception:
        pass
