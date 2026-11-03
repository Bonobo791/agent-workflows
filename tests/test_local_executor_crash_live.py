"""Opt-in crash probe using the signed-in local app server and real user scopes."""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


def _cleanup_scope(backend,unit,invocation,cgroup):
    from runner.local_scope import valid_scope_identity
    if not re.fullmatch(r'agent-workflows-[0-9a-f]{48}\.scope',unit) or not valid_scope_identity(unit,invocation,cgroup):
        raise RuntimeError('test scope identity is unverified')
    state=backend.inspect(unit)
    if state['active']=='active':
        if (state['id'],state['load'],state['invocation'],state['cgroup'])!=(unit,'loaded',invocation,cgroup):
            raise RuntimeError('test scope identity changed')
        if backend.stop(unit) is not True:raise RuntimeError('test scope stop is unverified')
        state=backend.inspect(unit)
    if (state['id']!=unit or state['active']!='inactive' or state['load'] not in ('loaded','not-found')
            or state['invocation'] not in ('',invocation) or backend.empty_cgroup(cgroup) is not True
            or backend.no_pending_job(unit) is not True):raise RuntimeError('test scope cleanup is unverified')


def cleanup_crash_fixture(temporary,backend):
    root=Path(temporary.name)
    try:
        if (root/'state.db').exists():
            with closing(sqlite3.connect(root/'state.db')) as db:
                present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_scopes'").fetchone()
                rows=db.execute("SELECT unit,invocation,cgroup FROM local_scopes WHERE state IS NOT 'terminated'").fetchall() if present else []
            for unit,invocation,cgroup in rows:
                _cleanup_scope(backend,unit,invocation,cgroup)
    except Exception as error:
        error.add_note('Private executor-crash fixture retained at '+str(root))
        raise
    temporary.cleanup()


class CrashFixtureCleanupTests(unittest.TestCase):
    def test_unverified_cleanup_retains_state_and_never_stops_changed_scope(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from tests.test_local_scope import Backend
        for failure in ('nonempty','identity','none'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);unit='agent-workflows-'+'a'*48+'.scope'
                with closing(sqlite3.connect(root/'state.db')) as db:
                    db.execute('CREATE TABLE local_scopes(unit TEXT,invocation TEXT,cgroup TEXT,state TEXT)')
                    db.execute('INSERT INTO local_scopes VALUES(?,?,?,?)',(unit,'a'*32,'/user.slice/'+unit,'running'));db.commit()
                backend=Backend();backend.unit=unit;backend.no_pending_job=lambda _:True
                if failure=='nonempty':backend.empty=False
                if failure=='identity':backend.invocation='b'*32
                temporary=SimpleNamespace(name=directory,cleanup=Mock())
                if failure=='none':
                    cleanup_crash_fixture(temporary,backend);temporary.cleanup.assert_called_once()
                else:
                    with self.assertRaises(RuntimeError):cleanup_crash_fixture(temporary,backend)
                    temporary.cleanup.assert_not_called();self.assertTrue((root/'state.db').exists())
                if failure=='identity':self.assertEqual(backend.stop_calls,0)
