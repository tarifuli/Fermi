"""Login throttling tests with isolated local SQLite state."""
import os
import tempfile
import unittest
from unittest.mock import patch

class LoginRateLimitTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'DATABASE_PATH':os.path.join(self.tmp.name,'limits.db')})
  self.env.start()
  from app import app,init_db
  self.app=app
  self.app.config['TESTING']=True
  init_db()
  self.client=app.test_client()
 def tearDown(self):
  self.env.stop()
  self.tmp.cleanup()
 def test_failed_logins_are_limited(self):
  for _ in range(8):
   r=self.client.post('/login',data={'email':'unknown@invalid.test','password':'wrong'})
   self.assertEqual(r.status_code,200)
  r=self.client.post('/login',data={'email':'unknown@invalid.test','password':'wrong'})
  self.assertEqual(r.status_code,429)
 def test_separate_email_not_locked(self):
  for _ in range(8):self.client.post('/login',data={'email':'a@invalid.test','password':'wrong'})
  r=self.client.post('/login',data={'email':'b@invalid.test','password':'wrong'})
  self.assertEqual(r.status_code,200)
if __name__=='__main__':unittest.main()
