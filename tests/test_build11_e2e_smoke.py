"""End-to-end authenticated onboarding smoke test using a disposable SQLite DB."""
import os
import re
import tempfile
import unittest
from unittest.mock import patch

class Build11E2ESmoke(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'DATABASE_PATH':os.path.join(self.tmp.name,'smoke.db')})
  self.env.start()
  from app import app,init_db
  self.app=app
  self.previous=(app.config.get('TESTING'),app.config.get('ENFORCE_CSRF_IN_TESTS'))
  app.config['TESTING']=True
  app.config['ENFORCE_CSRF_IN_TESTS']=True
  init_db()
  self.client=app.test_client()
 def tearDown(self):
  self.app.config['TESTING'],self.app.config['ENFORCE_CSRF_IN_TESTS']=self.previous
  self.env.stop()
  self.tmp.cleanup()
 def token(self):
  with self.client.session_transaction() as sess:return sess['_csrf_token']
 def test_signup_login_logout_and_cross_site_rejection(self):
  response=self.client.get('/signup')
  self.assertEqual(response.status_code,200)
  self.assertIn(b'csrf-token',response.data)
  token=self.token()
  self.assertEqual(self.client.post('/signup',data={'name':'Test Owner','email':'owner@smoke.test','password':'valid-password','confirm_password':'valid-password'}).status_code,403)
  response=self.client.post('/signup',data={'name':'Test Owner','email':'owner@smoke.test','password':'valid-password','confirm_password':'valid-password','_csrf_token':token})
  self.assertEqual(response.status_code,302)
  response=self.client.get('/logout')
  self.assertEqual(response.status_code,302)
  self.client.get('/login')
  token=self.token()
  response=self.client.post('/login',data={'email':'owner@smoke.test','password':'valid-password','_csrf_token':token},headers={'Origin':'https://evil.example'})
  self.assertEqual(response.status_code,403)
  response=self.client.post('/login',data={'email':'owner@smoke.test','password':'valid-password','_csrf_token':token})
  self.assertEqual(response.status_code,302)
  with self.client.session_transaction() as sess:self.assertIn('user_id',sess)
  response=self.client.get('/logout')
  self.assertEqual(response.status_code,302)
  with self.client.session_transaction() as sess:self.assertNotIn('user_id',sess)
if __name__=='__main__':unittest.main()
