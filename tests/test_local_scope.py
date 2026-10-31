import tempfile
import unittest
import sqlite3
import os
from runner.local_scope import LocalScopes, ScopeError

class Backend:
    def __init__(self): self.unit=None; self.state='active'; self.invocation='a'*32; self.stop_calls=0; self.empty=True
    def inspect(self,unit):
        if self.unit != unit: return {'id':unit,'load':'not-found','active':'inactive','invocation':'','cgroup':''}
        return {'id':unit,'load':'loaded' if self.state=='active' else 'not-found','active':self.state,'invocation':self.invocation if self.state=='active' else '', 'cgroup':'/user.slice/'+unit if self.state=='active' else ''}
    def stop(self,unit): self.stop_calls+=1; self.state='inactive'; return True
    def empty_cgroup(self,path): return self.empty

class ScopeTests(unittest.TestCase):
    def test_missing_events_in_existing_cgroup_is_not_termination_evidence(self):
        from pathlib import Path
        from unittest.mock import patch
        from runner.local_scope import SystemdScopes
        with tempfile.TemporaryDirectory() as root:
            base=Path(root); (base/'cgroup.controllers').write_text('cpu')
            group=base/'user.slice'/'run.scope'; group.mkdir(parents=True)
            def cgroup_path(value):
                path=Path(value)
                return base/path.relative_to('/sys/fs/cgroup') if path.is_relative_to('/sys/fs/cgroup') else path
            with patch('runner.local_scope.Path',side_effect=cgroup_path):
                backend=SystemdScopes({})
                self.assertFalse(backend.empty_cgroup('/user.slice/run.scope'))
                (group/'cgroup.events').write_text('populated 1\n')
                self.assertFalse(backend.empty_cgroup('/user.slice/run.scope'))
                (group/'cgroup.events').write_text('populated 0\n')
                self.assertTrue(backend.empty_cgroup('/user.slice/run.scope'))
                (group/'cgroup.events').unlink(); group.rmdir()
                self.assertTrue(backend.empty_cgroup('/user.slice/run.scope'))

    def test_stop_stays_unresolved_when_cgroup_events_are_missing(self):
        from pathlib import Path
        from unittest.mock import patch
        from runner.local_scope import SystemdScopes
        with tempfile.TemporaryDirectory() as root:
            base=Path(root)/'cgroups';base.mkdir();(base/'cgroup.controllers').write_text('cpu')
            backend=Backend();systemd=SystemdScopes({})
            backend.empty_cgroup=systemd.empty_cgroup
            scopes=LocalScopes(root+'/state.db',backend)
            try:
                unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit)
                (base/'user.slice'/unit).mkdir(parents=True)
                def cgroup_path(value):
                    path=Path(value)
                    return base/path.relative_to('/sys/fs/cgroup') if path.is_relative_to('/sys/fs/cgroup') else path
                with patch('runner.local_scope.Path',side_effect=cgroup_path):
                    with self.assertRaises(ScopeError): scopes.stop('run',unit)
                self.assertEqual(scopes.state('run',unit),'stopping')
            finally: scopes.close()

    def test_conflicting_legacy_unknown_scope_states_fail_closed_without_data_loss(self):
        with tempfile.TemporaryDirectory() as root:
            path=root+'/state.db';db=sqlite3.connect(path)
            db.execute('CREATE TABLE local_scopes(run TEXT,unit TEXT PRIMARY KEY,invocation TEXT,cgroup TEXT,state TEXT)')
            db.executemany('INSERT INTO local_scopes(run,unit,state) VALUES(?,?,?)',[('r','unknown.scope',None),('r','running.scope','running')])
            db.commit();db.close()
            with self.assertRaises(ScopeError):LocalScopes(path,Backend())
            db=sqlite3.connect(path)
            try:self.assertEqual(db.execute('SELECT unit,state FROM local_scopes ORDER BY unit').fetchall(),[('running.scope','running'),('unknown.scope',None)])
            finally:db.close()

    @unittest.skipUnless(os.environ.get('WORKFLOW_TEST_SYSTEMD')=='1','real user systemd scope not configured')
    def test_control_inspection_observes_real_scope_without_changing_state(self):
        import subprocess
        import time
        from types import SimpleNamespace
        from runner.control import ControlPlane
        from runner.local_scope import SystemdScopes
        from runner.manual_lifecycle import ManualLifecycle
        from runner.service import Service
        with tempfile.TemporaryDirectory() as root:
            path=root+'/state.db';service=Service(root);lifecycle=ManualLifecycle(path,'target','incarnation');service.lifecycle=lifecycle
            backend=SystemdScopes(os.environ);service.model_supervisor=SimpleNamespace(backend=backend)
            lifecycle.db.execute("INSERT INTO manual_assignments(run,target,incarnation,state) VALUES('run','target','incarnation','pending')")
            scopes=LocalScopes(path,backend);unit=scopes.reserve('run')
            launcher=subprocess.Popen(['/usr/bin/systemd-run','--user','--scope','--quiet','--unit',unit,'--','/usr/bin/sleep','120'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+10
                while True:
                    try: scopes.bind('run',unit);break
                    except ScopeError:
                        if launcher.poll() is not None or time.monotonic()>=deadline: raise
                        time.sleep(.05)
                control=ControlPlane(service,'secret');request={'action':'inspect_local_scope','run':'run','unit':unit}
                active=control.handle('secret',request)
                self.assertEqual(active['recorded']['unit'],unit);self.assertEqual(active['observed']['id'],unit)
                self.assertEqual(active['recorded']['invocation'],active['observed']['invocation'])
                self.assertEqual(active['recorded']['cgroup'],active['observed']['cgroup'])
                self.assertEqual(active['observed']['active'],'active');self.assertIs(active['cgroup_empty'],False)
                self.assertTrue(backend.stop(unit))
                stopped=control.handle('secret',request)
                self.assertEqual(stopped['recorded']['state'],'running')
                self.assertEqual(stopped['observed']['active'],'inactive');self.assertIs(stopped['cgroup_empty'],True)
                self.assertEqual(scopes.state('run',unit),'running')
            finally:
                subprocess.run(['/usr/bin/systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
                try: launcher.wait(timeout=5)
                except subprocess.TimeoutExpired: launcher.kill();launcher.wait(timeout=5)
                scopes.close();lifecycle.close();service.close()

    def test_control_inspects_exact_recorded_scope_without_reconciling(self):
        from types import SimpleNamespace
        from runner.control import ControlPlane, ControlError
        from runner.manual_lifecycle import ManualLifecycle
        from runner.service import Service
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            path=root+'/state.db'; backend=Backend(); service=Service(root)
            lifecycle=ManualLifecycle(path,'target','incarnation');service.lifecycle=lifecycle
            try:
                service.model_supervisor=SimpleNamespace(backend=backend)
                lifecycle.db.execute("INSERT INTO manual_assignments(run,target,incarnation,state) VALUES('run','target','incarnation','pending')")
                scopes=LocalScopes(path,backend)
                try:
                    unit=scopes.reserve('run');backend.unit=unit;scopes.bind('run',unit)
                    control=ControlPlane(service,'secret')
                    with self.assertRaises(ControlError): control.handle('wrong',{'action':'inspect_local_scope','run':'run','unit':unit})
                    result=control.handle('secret',{'action':'inspect_local_scope','run':'run','unit':unit})
                    self.assertEqual(result['recorded'],{'unit':unit,'state':'running','invocation':'a'*32,'cgroup':'/user.slice/'+unit})
                    self.assertEqual(result['observed']['active'],'active')
                    self.assertIs(result['cgroup_empty'],True)
                    self.assertEqual(scopes.state('run',unit),'running')
                    backend.invocation='b'*32;backend.empty=False
                    changed=control.handle('secret',{'action':'inspect_local_scope','run':'run','unit':unit})
                    self.assertEqual(changed['recorded']['invocation'],'a'*32)
                    self.assertEqual(changed['observed']['invocation'],'b'*32)
                    self.assertIs(changed['cgroup_empty'],False)
                    self.assertEqual(scopes.state('run',unit),'running')
                    with patch.object(backend,'inspect',side_effect=ScopeError('unavailable')):
                        unavailable=control.handle('secret',{'action':'inspect_local_scope','run':'run','unit':unit})
                    self.assertEqual(unavailable['recorded'],result['recorded'])
                    self.assertIsNone(unavailable['observed']);self.assertIsNone(unavailable['cgroup_empty'])
                    self.assertEqual(scopes.state('run',unit),'running')
                    with self.assertRaises(ControlError): control.handle('secret',{'action':'inspect_local_scope','run':'run','unit':'other.scope'})
                    with self.assertRaises(ControlError): control.handle('secret',{'action':'inspect_local_scope','run':'other','unit':unit})
                finally: scopes.close()
            finally: service.close();lifecycle.close()
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
