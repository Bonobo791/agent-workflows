"""Explicit one-page audit delivery; no background service or default endpoint."""
import argparse
import json
from contextlib import ExitStack
import os
from pathlib import Path
import sqlite3
import stat
import sys
from urllib.parse import quote

from runner.audit_export import TABLES, deliver_page
from runner.opik_export import create_opik_trace_exporter
from runner.worker import read_secret


def _private_database(path,create):
    path=Path(path)
    if not path.is_absolute() or path.parent.resolve(strict=True)!=path.parent or path.name in ('','.','..'):
        raise ValueError('canonical database path required')
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(directory)
        if info.st_uid!=os.geteuid() or info.st_mode&0o077: raise ValueError('private database directory required')
        flags=os.O_RDWR|os.O_CREAT if create else os.O_RDONLY
        fd=os.open(path.name,flags|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_nlink!=1: raise ValueError('private database required')
        finally: os.close(fd)
    finally: os.close(directory)
    return str(path)


def deliver_tables(source,cursor,key_file,endpoint,tables,limit=100,token_file=None):
    if not isinstance(tables,(list,tuple)) or not 1<=len(tables)<=8 or any(not isinstance(table,str) or table not in TABLES for table in tables) or len(set(tables))!=len(tables) or type(limit) is not int or not 1<=limit<=500: raise ValueError('invalid audit selection')
    source=_private_database(source,False); cursor=_private_database(cursor,True)
    key=read_secret(key_file).encode('utf-8')
    import requests
    with ExitStack() as cleanup:
        session=requests.Session(); session.trust_env=False; cleanup.callback(session.close)
        if token_file is not None: session.headers['Authorization']='Bearer '+read_secret(token_file)
        adapter=create_opik_trace_exporter(endpoint,session); cleanup.callback(adapter.exporter.shutdown)
        audit=sqlite3.connect('file:'+quote(source,safe='/')+'?mode=ro',uri=True,isolation_level=None); cleanup.callback(audit.close)
        present={row[0] for row in audit.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(tables)<=present: raise ValueError('selected audit table unavailable')
        progress=sqlite3.connect(cursor,isolation_level=None); cleanup.callback(progress.close)
        results={}
        for table in tables:
            result=deliver_page(audit,progress,endpoint,table,limit,key,adapter)
            results[table]=result
            if result['status'] not in ('empty','exported','superseded'): break
        return results


def deliver_once(source,cursor,key_file,endpoint,table,limit=100,token_file=None):
    return deliver_tables(source,cursor,key_file,endpoint,[table],limit,token_file)[table]


def main():
    parser=argparse.ArgumentParser(description='Deliver one page of private workflow audit events.')
    parser.add_argument('--source',required=True); parser.add_argument('--cursor',required=True)
    parser.add_argument('--key-file',required=True); parser.add_argument('--endpoint',required=True)
    parser.add_argument('--table',required=True,action='append',choices=sorted(TABLES)); parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--token-file')
    args=parser.parse_args()
    results=deliver_tables(args.source,args.cursor,args.key_file,args.endpoint,args.table,args.limit,args.token_file)
    print(next(iter(results.values()))['status'] if len(args.table)==1 else json.dumps(results,sort_keys=True,separators=(',',':')))
    return 0 if len(results)==len(args.table) and all(result['status'] in ('empty','exported','superseded') for result in results.values()) else 1


if __name__=='__main__':
    try: sys.exit(main())
    except Exception:
        print('Audit delivery unavailable.',file=sys.stderr); sys.exit(1)
