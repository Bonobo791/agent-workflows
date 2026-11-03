"""Observe desired remote state after the exact disposable process has exited.

A positive observation records current state, not causal attribution or proof
that GitHub has finished processing a disconnected request. Negative outcomes
remain uncertain.
"""

class PRRemoteObserver:
    def __init__(self,service,http): self.service,self.http=service,http
    @staticmethod
    def _matches_pr(value,target,number):
        try:
            return value['number']==number and value['state']=='open' and value['head']['sha']==target['head'] and value['head']['ref']==target['branch'] and value['head']['repo']['full_name']==target['repo'] and value['base']['ref']==target['base'] and value['base']['repo']['full_name']==target['repo']
        except (KeyError,TypeError): return False
