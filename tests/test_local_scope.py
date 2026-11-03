import tempfile
import unittest
import sqlite3
import os


class Backend:
    def __init__(self): self.unit=None; self.state='active'; self.invocation='a'*32; self.stop_calls=0; self.empty=True
    def inspect(self,unit):
        if self.unit != unit: return {'id':unit,'load':'not-found','active':'inactive','invocation':'','cgroup':''}
        return {'id':unit,'load':'loaded' if self.state=='active' else 'not-found','active':self.state,'invocation':self.invocation if self.state=='active' else '', 'cgroup':'/user.slice/'+unit if self.state=='active' else ''}
    def stop(self,unit): self.stop_calls+=1; self.state='inactive'; return True
    def empty_cgroup(self,path): return self.empty

