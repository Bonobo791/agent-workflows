"""Dispatch one claimed job through an injected, trusted lifecycle supervisor.

No concrete supervisor is configured. A start exception is uncertain, not proof
that no container started. Token-bearing dispatch data must not be logged.
"""
class SchedulerError(RuntimeError): pass

class Scheduler:
    def __init__(self,queue,supervisor,worker_id,ttl=60,fixture_mode=False):
        if not worker_id or ttl<=0: raise ValueError('worker and positive lease required')
        self.queue,self.supervisor,self.worker_id,self.ttl=queue,supervisor,worker_id,ttl
        self.fixture_mode=fixture_mode
    def dispatch(self):
        if not self.fixture_mode: raise SchedulerError('dispatch disabled until an independently verified lifecycle supervisor is configured')
        claim=self.queue.claim(self.worker_id,self.ttl)
        if claim is None: return None
        try:
            started=self.supervisor.start_guarded(claim['run'],claim['token'],lambda:self.queue.may_start(claim['run'],claim['token']))
            if started is not True and started is not False: raise SchedulerError('invalid supervisor response')
            if started is False: return None
        except Exception:
            try: self.queue.mark_uncertain(claim['run'],claim['token'])
            except Exception: pass
            raise SchedulerError('worker start outcome uncertain; inspect before retry') from None
        return claim
