import tempfile
import unittest

from runner.graph import GraphError, GraphStore, marketing_definition
from runner.queue import JobQueue


class ReviewContextMigrationTests(unittest.TestCase):
    def test_suppressed_deletion_rolls_back_review_and_package(self):
        with tempfile.TemporaryDirectory() as root:
            path=root+'/state.db'; graph=GraphStore(path); queue=JobQueue(path)
            try:
                graph.register(marketing_definition()); graph.create('r','marketing'); claim=graph.claim('r',60)
                queue.enqueue('r'); queue_claim=queue.claim('worker',60)['token']
                graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,artifact_ref TEXT)')
                graph.db.execute("INSERT INTO task_input_versions VALUES('r','task')")
                graph.db.executemany('INSERT INTO graph_stages VALUES(?,?,?,?,?)',[
                    ('r','freeze','passed','frozen',1),('r','linkedin_review','passed','review',2),
                    ('r','assembly','awaiting_review','package',3)])
                graph.db.execute("CREATE TRIGGER ignore_review_delete BEFORE DELETE ON graph_stages WHEN OLD.stage='linkedin_review' BEGIN SELECT RAISE(IGNORE); END")
                with self.assertRaises(GraphError):
                    graph.invalidate_legacy_channel_reviews('r',claim,queue_claim,{'linkedin_review':'review'},'frozen','task')
                self.assertEqual(graph.get('r','linkedin_review')['version'],'review')
                self.assertEqual(graph.get('r','assembly')['version'],'package')
                self.assertEqual(graph.db.execute('SELECT count(*) FROM invalidation_audit').fetchone()[0],0)
            finally: queue.close(); graph.close()
