"""Build 10 business settings and onboarding regression tests."""
import os
import tempfile
import unittest
from unittest.mock import patch

class BusinessSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'DATABASE_PATH':os.path.join(self.tmp.name,'onboarding.db')})
        self.env.start()
        from app import app,init_db,get_db
        app.config['TESTING']=True
        init_db()
        with get_db() as db:
            db.execute("INSERT INTO users(name,email,password) VALUES('Owner','owner@onboarding.test','unused')")
            self.uid=db.execute("SELECT id FROM users WHERE email='owner@onboarding.test'").fetchone()[0]
            db.execute("INSERT INTO businesses(name) VALUES('My Store')")
            self.bid=db.execute("SELECT id FROM businesses WHERE name='My Store'").fetchone()[0]
            db.execute("INSERT INTO business_memberships(business_id,user_id,role) VALUES (?,?,'owner')",(self.bid,self.uid))
        self.client=app.test_client()
        with self.client.session_transaction() as sess:sess['user_id']=self.uid

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def payload(self):
        return {'name':'Updated Store','contact_email':'store@example.test','contact_phone':'12345678',
                'address':'Dhaka','low_stock_threshold':7}

    def test_settings_update_and_data_driven_progress(self):
        from app import get_db
        from onboarding import onboarding_status
        self.assertEqual(self.client.get('/business/settings').status_code,200)
        with get_db() as db:
            self.assertEqual(onboarding_status(db,self.bid)['completed'],0)
        response=self.client.post('/business/settings',json=self.payload())
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        with get_db() as db:
            self.assertEqual(db.execute('SELECT name FROM businesses WHERE id=?',(self.bid,)).fetchone()[0],'Updated Store')
            self.assertEqual(db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',(self.bid,)).fetchone()[0],7)
            self.assertEqual(onboarding_status(db,self.bid)['completed'],2)
        self.assertIn(b'Updated Store',self.client.get('/business/profile').data)
        self.assertIn(b'Finish setting up',self.client.get('/').data)

    def test_validation_and_owner_only(self):
        from app import get_db
        bad={**self.payload(),'low_stock_threshold':-1}
        self.assertEqual(self.client.post('/business/settings',json=bad).status_code,400)
        self.assertEqual(self.client.post('/business/settings',json={**self.payload(),'contact_email':'invalid'}).status_code,400)
        with get_db() as db:
            db.execute("UPDATE business_memberships SET role='viewer' WHERE user_id=?",(self.uid,))
        self.assertEqual(self.client.post('/business/settings',json=self.payload()).status_code,403)
        with get_db() as db:
            self.assertEqual(db.execute('SELECT name FROM businesses WHERE id=?',(self.bid,)).fetchone()[0],'My Store')

if __name__=='__main__':unittest.main()
