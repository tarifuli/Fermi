import hashlib
import unittest
from app import app, hash_password, verify_password


class PasswordSecurityTests(unittest.TestCase):
    def test_new_password_is_not_sha256(self):
        stored = hash_password('a long password')
        self.assertNotEqual(stored, hashlib.sha256(b'a long password').hexdigest())
        self.assertTrue(verify_password(stored, 'a long password'))
        self.assertFalse(verify_password(stored, 'wrong password'))

    def test_existing_sha256_password_still_works(self):
        legacy = hashlib.sha256(b'old password').hexdigest()
        self.assertTrue(verify_password(legacy, 'old password'))
        self.assertFalse(verify_password(legacy, 'wrong password'))

    def test_cookie_security_defaults(self):
        self.assertTrue(app.config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(app.config['SESSION_COOKIE_SAMESITE'], 'Lax')


if __name__ == '__main__':
    unittest.main()
