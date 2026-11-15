"""Dashboard onboarding navigation and setup dialog regression coverage."""
import os,tempfile,unittest
from unittest.mock import patch

class DashboardOnboardingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'DATABASE_PATH':os.path.join(self.tmp.name,'dashboard.db')})
  self.env.start()
  from app import app,init_db,get_db
  app.config['TESTING']=True
  init_db()
  with get_db() as db:
   db.execute("INSERT INTO users(name,email,password) VALUES('Owner','dashboard@test.local','unused')")
   self.uid=db.execute("SELECT id FROM users WHERE email='dashboard@test.local'").fetchone()[0]
  self.client=app.test_client()
  with self.client.session_transaction() as sess:sess['user_id']=self.uid
 def tearDown(self):
  self.env.stop();self.tmp.cleanup()
 def test_new_user_sees_setup_dialog_and_button(self):
  page=self.client.get('/')
  self.assertEqual(page.status_code,200,page.get_data(as_text=True))
  self.assertIn(b'id="openSetupDialog"',page.data)
  self.assertIn(b'id="businessSetupDialog"',page.data)
  self.assertIn(b'id="dashboardBusinessSettings"',page.data)
 def test_existing_business_always_sees_checklist_and_settings(self):
  from app import get_db
  with get_db() as db:
   db.execute("INSERT INTO businesses(name) VALUES('Dashboard Shop')")
   bid=db.execute("SELECT id FROM businesses WHERE name='Dashboard Shop'").fetchone()[0]
   db.execute("INSERT INTO business_memberships(business_id,user_id,role) VALUES (?,?,'owner')",(bid,self.uid))
  page=self.client.get('/')
  self.assertEqual(page.status_code,200,page.get_data(as_text=True))
  self.assertIn(b'id="dashboardOnboarding"',page.data)
  self.assertIn(b'Open Business Settings',page.data)
  with get_db() as db:
   db.execute("INSERT INTO business_details(business_id,contact_email,contact_phone,address) VALUES (?,?,?,?)",(bid,'hello@shop.test','123','Dhaka'))
   db.execute("INSERT INTO inventory_settings(business_id,low_stock_threshold) VALUES (?,?)",(bid,5))
   db.execute("INSERT INTO products(business_id,name,sku,price,stock) VALUES (?,?,?,?,?)",(bid,'Pen','PEN-1',10,2))
   db.execute("INSERT INTO invoices(business_id,invoice_number,customer_name,subtotal,tax,total,status) VALUES (?,?,?,?,?,?,?)",(bid,'DASH-001','Guest',10,0,10,'paid'))
  page=self.client.get('/')
  self.assertEqual(page.status_code,200,page.get_data(as_text=True))
  self.assertIn(b'id="dashboardOnboarding"',page.data)
  self.assertIn(b'4 of 4 completed',page.data)
if __name__=='__main__':unittest.main()
