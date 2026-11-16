"""Build 11 browser security and session hardening regression tests."""
import unittest
from app import app

class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.client=app.test_client()
    def test_response_headers(self):
        response=self.client.get('/login')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'],'DENY')
        self.assertIn('strict-origin',response.headers['Referrer-Policy'])
    def test_request_size_limit(self):
        self.assertEqual(app.config['MAX_CONTENT_LENGTH'], 2 * 1024 * 1024)
        response=self.client.post('/login',data={'email':'a'* (2*1024*1024), 'password':'x'})
        self.assertEqual(response.status_code,413)
    def test_auth_page_not_cached(self):
        response=self.client.get('/login')
        self.assertIn('no-store',response.headers['Cache-Control'])
    def test_cross_site_post_blocked(self):
        response=self.client.post('/login',headers={'Sec-Fetch-Site':'cross-site'},data={'email':'x','password':'x'})
        self.assertEqual(response.status_code,403)
        response=self.client.post('/login',headers={'Origin':'https://attacker.example'},data={})
        self.assertEqual(response.status_code,403)
    def test_same_origin_request_not_blocked(self):
        response=self.client.post('/login',headers={'Origin':'http://localhost'},data={'email':'missing@example.com','password':'bad'})
        self.assertEqual(response.status_code,200)
    def test_authenticated_pages_not_cached(self):
        with self.client.session_transaction() as sess:sess['user_id']=999
        response=self.client.get('/login')
        self.assertIn('no-store',response.headers.get('Cache-Control',''))
    def test_session_lifetime_is_bounded(self):
        self.assertLessEqual(app.permanent_session_lifetime.total_seconds(),12*3600)

if __name__=='__main__':unittest.main()
