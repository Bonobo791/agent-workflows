import os
import tempfile
import unittest
from runner.credentials import CredentialStore, CredentialError

class CredentialTests(unittest.TestCase):
    def test_locked_preserves_caller_oserror_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CredentialStore(directory)
            failure=OSError('caller failure')
            with self.assertRaises(OSError) as caught:
                with store.locked(): raise failure
            self.assertIs(caught.exception,failure)
            with store.locked() as record: record.save({'value':'synthetic'})
    def test_atomic_private_round_trip_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CredentialStore(directory)
            with store.locked() as record:
                record.save({'access_token':'synthetic'})
                self.assertEqual(record.load()['access_token'],'synthetic')
            self.assertEqual(os.stat(os.path.join(directory,'credentials.json')).st_mode & 0o777, 0o600)
            os.unlink(os.path.join(directory,'credentials.json'))
            os.symlink('/dev/null', os.path.join(directory,'credentials.json'))
            with store.locked() as record:
                with self.assertRaises(CredentialError): record.load()
    def test_rejects_world_accessible_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory,0o755)
            with self.assertRaises(CredentialError): CredentialStore(directory)
