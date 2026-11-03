"""Stops a live test's unresolved scopes, then removes its private fixture folder."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3
from runner.local_scope import STOPPED_STATES


def _cleanup_scope(backend,unit):
    if not re.fullmatch(r'agent-workflows-[0-9a-f]{48}\.scope',unit): raise RuntimeError('test scope name is unverified')
    state=backend.inspect(unit)
    if state['active'] not in STOPPED_STATES:
        if backend.stop(unit) is not True: raise RuntimeError('test scope stop is unverified')
        state=backend.inspect(unit)
    if state['active'] not in STOPPED_STATES: raise RuntimeError('test scope cleanup is unverified')


def cleanup_crash_fixture(temporary,backend):
    root=Path(temporary.name)
    try:
        if (root/'state.db').exists():
            with closing(sqlite3.connect(root/'state.db')) as db:
                present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_scopes'").fetchone()
                rows=db.execute("SELECT unit FROM local_scopes WHERE state IS NOT 'terminated'").fetchall() if present else []
            for (unit,) in rows: _cleanup_scope(backend,unit)
    except Exception as error:
        error.add_note('Private fixture retained at '+str(root))
        raise
    temporary.cleanup()
