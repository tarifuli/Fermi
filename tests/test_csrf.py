"""CSRF enforcement checks independent of legacy test fixtures."""
import unittest
from app import app

class CSRFTests(unittest.TestCase):
 def setUp(self):
  self.old=app.config.get('ENFORCE_CSRF_IN_TESTS')
  app.config['ENFORCE_CSRF_IN_TESTS']=True
  self.client=app.test_client()
 def tearDown(self):
  app.config['ENFORCE_CSRF_IN_TESTS']=self.old
 def test_missing_token_rejected(self):
  self.client.get('/login')
  self.assertEqual(self.client.post('/login',data={'email':'x','password':'x'}).status_code,403)
 def test_valid_form_token(self):
  self.client.get('/login')
  with self.client.session_transaction() as sess:token=sess['_csrf_token']
  response=self.client.post('/login',data={'email':'nobody@example.test','password':'wrong','_csrf_token':token})
  self.assertEqual(response.status_code,200)
 def test_invalid_token_rejected(self):
  self.client.get('/login')
  self.assertEqual(self.client.post('/signup',data={'_csrf_token':'invalid'}).status_code,403)
if __name__=='__main__':unittest.main()
