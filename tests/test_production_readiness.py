"""Build 11 production readiness checks for error handling and route registration."""
import unittest
from app import app

class ReadinessTests(unittest.TestCase):
 def setUp(self):self.client=app.test_client()
 def test_all_business_blueprints_registered(self):
  self.assertIn('onboarding.business_settings',app.view_functions)
  self.assertTrue(any(rule.rule=='/returns' for rule in app.url_map.iter_rules()))
 def test_chat_rejects_invalid_json(self):
  with self.client.session_transaction() as s:s['user_id']=1
  response=self.client.post('/api/chat',data='not-json',content_type='application/json')
  self.assertEqual(response.status_code,400)
  self.assertFalse(response.json['success'])
 def test_chat_rejects_oversized_input(self):
  with self.client.session_transaction() as s:s['user_id']=1
  response=self.client.post('/api/chat',json={'message':'a'*4001})
  self.assertEqual(response.status_code,400)
 def test_logout_clears_session(self):
  with self.client.session_transaction() as s:s['user_id']=1;s['user_name']='Test'
  response=self.client.get('/logout')
  self.assertEqual(response.status_code,302)
  with self.client.session_transaction() as s:self.assertNotIn('user_id',s)
if __name__=='__main__':unittest.main()
