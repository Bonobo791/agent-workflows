import tempfile
import unittest
import sqlite3
from runner.local_scope import LocalScopes, ScopeError

class Backend:
    def __init__(self): self.unit=None; self.state='active'; self.invocation='a'*32; self.stop_calls=0; self.empty=True
    def inspect(self,unit):
        if self.unit != unit: return {'id':unit,'load':'not-found','active':'inactive','invocation':'','cgroup':''}
        return {'id':unit,'load':'loaded' if self.state=='active' else 'not-found','active':self.state,'invocation':self.invocation if self.state=='active' else '', 'cgroup':'/user.slice/'+unit if self.state=='active' else ''}
    def stop(self,unit): self.stop_calls+=1; self.state='inactive'; return True
    def empty_cgroup(self,path): return self.empty

class ScopeTests(unittest.TestCase):
    def test_scope_turn_audit_correlates_without_exposing_identifiers(self):
        from runner.audit_export import read_events
        from runner.local_supervisor import LocalSupervisor
        from runner.otel_audit import _attributes
        with tempfile.TemporaryDirectory() as root:
            backend=Backend();db=root+'/state.db'
            LocalSupervisor(db,'/bin/true',{},backend,lambda _:True,lambda *_:root,lambda:True,{},policy_hash='a'*64)
            scopes=LocalScopes(db,backend);unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit);scopes.stop('run',unit)
            scopes.db.execute("CREATE TRIGGER fail_turn_audit BEFORE INSERT ON local_turn_scope_audit BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                scopes.db.execute('INSERT INTO local_turn_scopes VALUES(?,?,?,?,?,?)',('run','platform_review','private-session','private-turn',unit,'binding'))
            self.assertEqual(scopes.db.execute('SELECT COUNT(*) FROM local_turn_scopes').fetchone()[0],0)
            scopes.db.execute('DROP TRIGGER fail_turn_audit')
            scopes.db.execute('INSERT INTO local_turn_scopes VALUES(?,?,?,?,?,?)',('run','platform_review','private-session','private-turn',unit,'binding'))
            states,_=read_events(scopes.db,'local_scope_audit',0,10,b'x'*32)
            links,_=read_events(scopes.db,'local_turn_scope_audit',0,10,b'x'*32)
            self.assertEqual(states[-1]['scope_id'],links[0]['scope_id']);self.assertEqual(links[0]['role'],'platform_review')
            self.assertIsNotNone(links[0]['turn_id']);self.assertEqual(_attributes(links[0])['scope_id'],states[-1]['scope_id'])
            for private in (unit,'private-session','private-turn'):self.assertNotIn(private,str(links))
            scopes.close()
    def test_scope_audit_replays_and_failure_prevents_stop_request(self):
        from runner.audit_export import read_events
        with tempfile.TemporaryDirectory() as root:
            backend=Backend();scopes=LocalScopes(root+'/state.db',backend)
            unit=scopes.reserve('private-run');backend.unit=unit;scopes.bind('private-run',unit)
            scopes.db.execute("CREATE TRIGGER reject_scope_audit BEFORE INSERT ON local_scope_audit BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
            with self.assertRaises(sqlite3.IntegrityError): scopes.stop('private-run',unit)
            self.assertEqual(scopes.state('private-run',unit),'running');self.assertEqual(backend.stop_calls,0)
            scopes.db.execute('DROP TRIGGER reject_scope_audit')
            self.assertTrue(scopes.stop('private-run',unit));scopes.close()
            scopes=LocalScopes(root+'/state.db',backend)
            events,cursor=read_events(scopes.db,'local_scope_audit',0,20,b'x'*32)
            self.assertEqual([event['status'] for event in events],['starting','running','stopping','terminated'])
            self.assertEqual(cursor,4);self.assertNotIn('private-run',str(events));self.assertNotIn(unit,str(events))
            scopes.close()
    def test_termination_audit_failure_leaves_stopped_scope_unresolved(self):
        with tempfile.TemporaryDirectory() as root:
            backend=Backend();scopes=LocalScopes(root+'/state.db',backend)
            unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit)
            scopes.db.execute("CREATE TRIGGER reject_termination BEFORE INSERT ON local_scope_audit WHEN NEW.state='terminated' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
            with self.assertRaises(ScopeError): scopes.stop('run',unit)
            self.assertEqual(backend.stop_calls,1);self.assertEqual(scopes.state('run',unit),'stopping')
            scopes.db.execute('DROP TRIGGER reject_termination')
            self.assertTrue(scopes.reconcile_stop('run',unit,lambda *_:True))
            self.assertEqual([row[0] for row in scopes.db.execute('SELECT state FROM local_scope_audit ORDER BY id')],['starting','running','stopping','terminated'])
            scopes.close()
    def test_scope_reservation_identity_and_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            backend=Backend(); scopes=LocalScopes(root+'/state.db',backend)
            unit=scopes.reserve('run'); backend.unit=unit; scopes.bind('run',unit)
            self.assertTrue(scopes.assert_active('run',unit))
            with self.assertRaises(ScopeError): scopes.reserve('run')
            self.assertTrue(scopes.stop('run',unit)); self.assertEqual(backend.stop_calls,1)
            self.assertEqual(scopes.state('run',unit),'terminated')
            scopes.close()
    def test_unstarted_scope_cannot_be_treated_as_stopped(self):
        with tempfile.TemporaryDirectory() as root:
            backend=Backend(); scopes=LocalScopes(root+'/state.db',backend); unit=scopes.reserve('run')
            with self.assertRaises(ScopeError): scopes.bind('run',unit)
            with self.assertRaises(ScopeError): scopes.stop('run',unit)
            self.assertEqual(scopes.state('run',unit),'starting'); self.assertEqual(backend.stop_calls,0)
            scopes.close()
            scopes=LocalScopes(root+'/state.db',backend)
            with self.assertRaises(ScopeError): scopes.reserve('run')
            self.assertEqual(scopes.state('run',unit),'starting'); scopes.close()
    def test_identity_change_or_nonempty_scope_remains_fenced(self):
        for case in ('identity','nonempty'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                backend=Backend(); scopes=LocalScopes(root+'/state.db',backend); unit=scopes.reserve('run'); backend.unit=unit; scopes.bind('run',unit)
                if case=='identity': backend.invocation='b'*32
                else: backend.empty=False
                with self.assertRaises(ScopeError): scopes.stop('run',unit)
                self.assertNotEqual(scopes.state('run',unit),'terminated'); scopes.close()
    def test_interrupted_stop_requires_quiescence_and_empty_cgroup(self):
        with tempfile.TemporaryDirectory() as root:
            backend=Backend(); scopes=LocalScopes(root+'/state.db',backend);unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit)
            backend.empty=False
            with self.assertRaises(ScopeError): scopes.stop('run',unit)
            self.assertEqual(scopes.state('run',unit),'stopping')
            backend.empty=True
            with self.assertRaises(ScopeError): scopes.reconcile_stop('run',unit,lambda *_:False)
            self.assertEqual(scopes.state('run',unit),'stopping')
            observations=iter([True,False])
            with self.assertRaises(ScopeError): scopes.reconcile_stop('run',unit,lambda *_:next(observations))
            self.assertEqual(scopes.state('run',unit),'stopping')
            self.assertTrue(scopes.reconcile_stop('run',unit,lambda *_:True))
            self.assertEqual(scopes.state('run',unit),'terminated');scopes.close()
