"""Build 16 owner/staff/viewer and account security regression tests."""
import os
import tempfile
import unittest
from unittest.mock import patch

class TeamPermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DATABASE_PATH': os.path.join(self.tmp.name, 'team.db')})
        self.env.start()
        from app import app, init_db, get_db, hash_password
        self.app = app
        self.prior = app.config['TESTING']
        app.config['TESTING'] = True
        init_db()
        with get_db() as db:
            for name, email in [('Owner','owner@team.test'),('Staff','staff@team.test'),('Viewer','viewer@team.test'),('Outside','outside@team.test')]:
                db.execute('INSERT INTO users(name,email,password) VALUES (?,?,?)',
                           (name,email,hash_password('Old-password-123')))
            db.execute('INSERT INTO businesses(name) VALUES (?)', ('Team Store',))
            self.bid = db.execute('SELECT id FROM businesses WHERE name=?', ('Team Store',)).fetchone()[0]
            self.users = {r['email']:r['id'] for r in db.execute('SELECT id,email FROM users').fetchall()}
            for email,role in [('owner@team.test','owner'),('staff@team.test','staff'),('viewer@team.test','viewer')]:
                db.execute('INSERT INTO business_memberships(business_id,user_id,role) VALUES (?,?,?)',
                           (self.bid,self.users[email],role))
            db.execute('INSERT INTO products(business_id,name,sku,category,price,stock) VALUES (?,?,?,?,?,?)',
                       (self.bid,'Test Product','TEAM-1','General',10,10))
        self.client = app.test_client()
        self.as_user('owner@team.test')

    def tearDown(self):
        self.app.config['TESTING'] = self.prior
        self.env.stop()
        self.tmp.cleanup()

    def as_user(self,email):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = self.users[email]

    def test_owner_can_manage_members_and_preserve_last_owner(self):
        self.assertEqual(self.client.get('/business/team').status_code,200)
        self.assertEqual(self.client.post('/business/team/members',json={'email':'outside@team.test','role':'viewer'}).status_code,200)
        outside=self.users['outside@team.test']
        self.assertEqual(self.client.post('/business/team/members',json={'email':'outside@team.test','role':'staff'}).status_code,409)
        self.assertEqual(self.client.patch(f'/business/team/members/{outside}',json={'role':'staff'}).status_code,200)
        self.assertEqual(self.client.delete(f'/business/team/members/{outside}').status_code,200)
        owner=self.users['owner@team.test']
        self.assertEqual(self.client.delete(f'/business/team/members/{owner}').status_code,409)
        self.assertEqual(self.client.patch(f'/business/team/members/{owner}',json={'role':'viewer'}).status_code,409)
        self.assertEqual(self.client.post('/business/team/members',json={'email':'unknown@team.test','role':'viewer'}).status_code,404)

    def test_viewer_is_read_only_and_staff_cannot_administer(self):
        self.as_user('viewer@team.test')
        self.assertEqual(self.client.get('/products').status_code,200)
        self.assertEqual(self.client.post('/invoices/create',json={'customer_name':'A','items':[]}).status_code,403)
        self.assertEqual(self.client.post('/business/settings',json={}).status_code,403)
        self.assertEqual(self.client.post('/business/team/members',json={'email':'outside@team.test','role':'staff'}).status_code,403)
        self.as_user('staff@team.test')
        self.assertEqual(self.client.get('/business/team').status_code,200)
        self.assertEqual(self.client.post('/business/team/members',json={'email':'outside@team.test','role':'viewer'}).status_code,403)
        self.assertEqual(self.client.post('/business/settings',json={'name':'Changed','contact_email':'','contact_phone':'','address':'','low_stock_threshold':20}).status_code,403)

    def test_password_change_requires_current_password_and_signs_out(self):
        self.assertEqual(self.client.post('/account/password',json={'current_password':'wrong','new_password':'New-password-123'}).status_code,403)
        self.assertEqual(self.client.post('/account/password',json={'current_password':'Old-password-123','new_password':'short'}).status_code,400)
        self.assertEqual(self.client.post('/account/password',json={'current_password':'Old-password-123','new_password':'New-password-123'}).status_code,200)
        self.assertIn('/login',self.client.get('/business/team').location)
        from app import get_db, verify_password
        with get_db() as db:
            hashed=db.execute('SELECT password FROM users WHERE id=?',(self.users['owner@team.test'],)).fetchone()[0]
        self.assertTrue(verify_password(hashed,'New-password-123'))
