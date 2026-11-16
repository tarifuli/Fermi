"""Cross-device password-change session revocation regression tests."""
import os
import tempfile
import unittest
from unittest.mock import patch


class SessionRevocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DATABASE_PATH': os.path.join(self.tmp.name, 'sessions.db')})
        self.env.start()
        from app import app, init_db, get_db, hash_password
        self.app = app
        self.old_testing = app.config['TESTING']
        self.old_binding = app.config.get('ENFORCE_SESSION_BINDING_IN_TESTS')
        self.old_csrf = app.config.get('ENFORCE_CSRF_IN_TESTS')
        app.config['TESTING'] = True
        app.config['ENFORCE_SESSION_BINDING_IN_TESTS'] = True
        app.config['ENFORCE_CSRF_IN_TESTS'] = False
        init_db()
        with get_db() as db:
            db.execute('INSERT INTO users(name,email,password) VALUES (?,?,?)',
                       ('Session User', 'session-revoke@example.test', hash_password('Old-password-123')))
        self.first = app.test_client()
        self.second = app.test_client()

    def tearDown(self):
        self.app.config['TESTING'] = self.old_testing
        if self.old_binding is None:
            self.app.config.pop('ENFORCE_SESSION_BINDING_IN_TESTS', None)
        else:
            self.app.config['ENFORCE_SESSION_BINDING_IN_TESTS'] = self.old_binding
        if self.old_csrf is None:
            self.app.config.pop('ENFORCE_CSRF_IN_TESTS', None)
        else:
            self.app.config['ENFORCE_CSRF_IN_TESTS'] = self.old_csrf
        self.env.stop()
        self.tmp.cleanup()

    def login(self, client, password='Old-password-123'):
        return client.post('/login', data={
            'email': 'session-revoke@example.test', 'password': password
        })

    def test_password_change_revokes_all_other_devices(self):
        self.assertEqual(self.login(self.first).status_code, 302)
        self.assertEqual(self.login(self.second).status_code, 302)
        self.assertEqual(self.first.get('/').status_code, 200)
        self.assertEqual(self.second.get('/').status_code, 200)

        changed = self.first.post('/account/password', json={
            'current_password': 'Old-password-123', 'new_password': 'New-password-456'
        })
        self.assertEqual(changed.status_code, 200)
        self.assertIn('/login', self.first.get('/').location)
        self.assertIn('/login', self.second.get('/').location)
        self.assertEqual(self.login(self.second, 'New-password-456').status_code, 302)
        self.assertEqual(self.second.get('/').status_code, 200)

    def test_legacy_unbound_session_is_rejected(self):
        self.assertEqual(self.login(self.first).status_code, 302)
        with self.first.session_transaction() as sess:
            sess.pop('_password_fingerprint', None)
        self.assertIn('/login', self.first.get('/').location)


if __name__ == '__main__':
    unittest.main()
