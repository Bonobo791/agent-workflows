"""Credential-free orchestration prototype; not a concurrent worker service."""
from runner.store import STAGES, Conflict

class StageBlocked(RuntimeError):
    pass

class Orchestrator:
    def __init__(self, store, specialist, max_attempts=2):
        if max_attempts < 1: raise ValueError('max_attempts must be positive')
        self.store, self.specialist, self.max_attempts = store, specialist, max_attempts
        self.store.db.execute('''CREATE TABLE IF NOT EXISTS attempts (
            run TEXT NOT NULL, stage TEXT NOT NULL, version TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY(run,stage,version))''')

    def execute(self, run, stage, version, inputs):
        if stage not in ('plan','implement','test','qa'): raise StageBlocked('external or unsupported stage')
        db=self.store.db
        state=db.execute('SELECT status FROM runs WHERE id=?',(run,)).fetchone()
        if not state or state[0] in ('cancelled','completed'): raise StageBlocked('run unavailable')
        done=dict(db.execute('SELECT name,version FROM stages WHERE run=?',(run,)))
        if done.get(stage) == version: return True
        index=STAGES.index(stage)
        if index and STAGES[index-1] not in done: raise StageBlocked('prerequisite missing')
        if stage in ('test','qa') and done.get('implement') != version: raise StageBlocked('commit mismatch')
        attempts=db.execute('SELECT count FROM attempts WHERE run=? AND stage=? AND version=?',(run,stage,version)).fetchone()
        if attempts and attempts[0] >= self.max_attempts: raise StageBlocked('retry limit reached')
        db.execute('INSERT INTO attempts VALUES (?,?,?,1) ON CONFLICT(run,stage,version) DO UPDATE SET count=count+1',(run,stage,version))
        session=self.store.get_session(run,'implement') if stage == 'implement' else None
        result=self.specialist.run(stage,inputs,session)
        if not isinstance(result,dict) or type(result.get('passed')) is not bool: raise StageBlocked('invalid specialist result')
        if stage == 'implement' and result.get('session_id'):
            if session and result['session_id'] != session: raise StageBlocked('implementation session changed unexpectedly')
            self.store.session(run,'implement',result['session_id'])
        if not result['passed']: return False
        try: self.store.advance(run,stage,version)
        except Conflict as exc: raise StageBlocked('stage could not be recorded') from exc
        return True
