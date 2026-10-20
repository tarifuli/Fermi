import hashlib
import os
import tempfile
import unittest

class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['DATABASE_PATH'] = os.path.join(self.tmp.name, 'test.db')
        os.environ['SECRET_KEY'] = 'test-secret-only'
        import app
        self.module = app
        self.client = app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_signup_logout_and_login(self):
        response = self.client.post('/signup', data={'name':'Test Owner','email':'owner@example.com','password':'secure-password-123','confirm_password':'secure-password-123'})
        self.assertEqual(response.status_code, 302)
        self.client.get('/logout')
        response = self.client.post('/login', data={'email':'owner@example.com','password':'secure-password-123'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/')

    def test_legacy_password_upgraded_on_login(self):
        legacy = hashlib.sha256(b'old-password').hexdigest()
        with self.module.get_db() as db:
            db.execute('INSERT INTO users (name,email,password) VALUES (?,?,?)', ('Legacy','legacy@example.com',legacy))
        response = self.client.post('/login', data={'email':'legacy@example.com','password':'old-password'})
        self.assertEqual(response.status_code,302)
        with self.module.get_db() as db:
            stored = db.execute('SELECT password FROM users WHERE email=?',('legacy@example.com',)).fetchone()[0]
        self.assertNotEqual(stored,legacy)
        self.assertTrue(self.module.verify_password(stored,'old-password'))

if __name__ == '__main__':
    unittest.main()
