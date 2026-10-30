"""Local-owner decisions bound to an exact review package; no publication."""
import secrets
import time

class HumanReviewError(RuntimeError): pass

class HumanReview:
    def __init__(self,service):
        self.service=service
        service.graph.db.execute('CREATE TABLE IF NOT EXISTS human_review_decisions(run TEXT NOT NULL,version TEXT NOT NULL,decision TEXT NOT NULL,actor TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(run,version))')
    def decide(self,run,queue_claim,graph_claim,version,decision):
        service=self.service
        if decision not in ('approved','rejected') or not isinstance(version,str): raise HumanReviewError('invalid review decision')
        if service.graph._definition(run).name!='marketing': raise HumanReviewError('human review package unavailable')
        if service.task_intake is None or not callable(getattr(service.task_intake,'revalidate',None)) or service.task_intake.revalidate(run) is not True: raise HumanReviewError('task freshness unavailable or changed')
        if not service.verify_terminal(run): raise HumanReviewError('review package evidence unavailable')
        db=service.graph.db; db.execute('BEGIN IMMEDIATE')
        try:
            service.graph._lease(run,graph_claim)
            row=db.execute('SELECT status,token,expires FROM jobs WHERE run=?',(run,)).fetchone()
            if not row or row[0]!='running' or not isinstance(row[1],str) or not isinstance(queue_claim,str) or not secrets.compare_digest(row[1],queue_claim) or row[2]<=time.time(): raise HumanReviewError('queue claim unavailable')
            stage=service.graph.get(run,'assembly')
            if not stage or stage['status']!='awaiting_review' or stage['version']!=version: raise HumanReviewError('review package changed')
            db.execute('INSERT INTO human_review_decisions VALUES(?,?,?,?,?)',(run,version,decision,'local-owner',time.time()))
            db.execute('COMMIT'); return {'decision':decision,'version':version,'actor':'local-owner'}
        except Exception: db.execute('ROLLBACK'); raise
    def current(self,run):
        stage=self.service.graph.get(run,'assembly')
        if not stage or stage['status']!='awaiting_review': return None
        row=self.service.graph.db.execute('SELECT decision,actor,created FROM human_review_decisions WHERE run=? AND version=?',(run,stage['version'])).fetchone()
        return {'version':stage['version'],'decision':row[0],'actor':row[1],'created':row[2]} if row else None
