import tempfile
import unittest

from runner.graph import GraphError, GraphStore, engineering_definition


class InvalidationAuditTests(unittest.TestCase):
    def test_suppressed_audit_rolls_back_invalidation(self):
        for table in ('invalidation_audit', 'graph_audit'):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as root:
                graph=GraphStore(root+'/state.db')
                try:
                    graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
                    graph.finish('r','intake',graph.prepare('r','intake',claim),'passed','v1',claim)
                    graph.db.execute(f"CREATE TRIGGER ignore_invalidation BEFORE INSERT ON {table} WHEN NEW.status='invalidated' BEGIN SELECT RAISE(IGNORE); END")
                    with self.assertRaises(GraphError): graph.invalidate('r','intake',graph.prepare('r','intake',claim),claim)
                    self.assertEqual(graph.get('r','intake')['version'],'v1')
                    self.assertEqual(graph.db.execute('SELECT count(*) FROM invalidation_audit').fetchone()[0],0)
                finally: graph.close()

    def test_modified_audit_rolls_back_invalidation(self):
        for table in ('invalidation_audit', 'graph_audit'):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as root:
                graph=GraphStore(root+'/state.db')
                try:
                    graph.register(engineering_definition()); graph.create('r','engineering'); claim=graph.claim('r',60)
                    graph.finish('r','intake',graph.prepare('r','intake',claim),'passed','v1',claim)
                    graph.db.execute(f"CREATE TRIGGER corrupt_invalidation AFTER INSERT ON {table} WHEN NEW.status='invalidated' BEGIN UPDATE {table} SET stage='other' WHERE id=NEW.id; END")
                    with self.assertRaises(GraphError): graph.invalidate('r','intake',graph.prepare('r','intake',claim),claim)
                    self.assertEqual(graph.get('r','intake')['version'],'v1')
                    self.assertEqual(graph.db.execute('SELECT count(*) FROM invalidation_audit').fetchone()[0],0)
                finally: graph.close()
