"""Build 11: enforce CSRF on actual inventory, POS, invoice and refund writes."""
import os
import tempfile
import unittest
from unittest.mock import patch

class ProtectedBusinessFlows(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'DATABASE_PATH':os.path.join(self.tmp.name,'flows.db')})
  self.env.start()
  from app import app,init_db,get_db
  self.app=app
  self.old=(app.config.get('TESTING'),app.config.get('ENFORCE_CSRF_IN_TESTS'))
  app.config.update(TESTING=True,ENFORCE_CSRF_IN_TESTS=True)
  init_db()
  with get_db() as db:
   db.execute("INSERT INTO users(name,email,password) VALUES('Owner','flow@test.local','unused')")
   uid=db.execute("SELECT id FROM users WHERE email='flow@test.local'").fetchone()[0]
   db.execute("INSERT INTO businesses(name) VALUES('Flow Shop')")
   self.bid=db.execute("SELECT id FROM businesses WHERE name='Flow Shop'").fetchone()[0]
   db.execute("INSERT INTO business_memberships(business_id,user_id,role) VALUES (?,?,'owner')",(self.bid,uid))
   db.execute("INSERT INTO products(business_id,name,sku,price,stock) VALUES (?,?,?,?,?)",(self.bid,'Notebook','FLOW-01',100,5))
   self.pid=db.execute("SELECT id FROM products WHERE sku='FLOW-01'").fetchone()[0]
  self.client=app.test_client()
  with self.client.session_transaction() as sess:sess['user_id']=uid
  self.client.get('/pos')
  with self.client.session_transaction() as sess:self.token=sess['_csrf_token']
 def tearDown(self):
  self.app.config['TESTING'],self.app.config['ENFORCE_CSRF_IN_TESTS']=self.old
  self.env.stop()
  self.tmp.cleanup()
 def test_pos_inventory_invoice_refund_csrf_and_replay(self):
  from app import get_db
  checkout={'idempotency_key':'flow-checkout-unique-001','customer_name':'Buyer','items':[{'product_id':self.pid,'quantity':2}]}
  self.assertEqual(self.client.post('/invoices/create',json=checkout).status_code,403)
  self.assertEqual(self.client.post('/products/adjust/'+str(self.pid),json={'delta':1,'reason':'Audit'}).status_code,403)
  headers={'X-CSRF-Token':self.token}
  adjust=self.client.post('/products/adjust/'+str(self.pid),json={'delta':1,'reason':'Audit'},headers=headers)
  self.assertEqual(adjust.status_code,200,adjust.get_data(as_text=True))
  sale=self.client.post('/invoices/create',json=checkout,headers=headers)
  self.assertEqual(sale.status_code,200,sale.get_data(as_text=True))
  replay=self.client.post('/invoices/create',json=checkout,headers=headers)
  self.assertEqual(replay.status_code,200)
  self.assertEqual(sale.json['invoice_id'],replay.json['invoice_id'])
  inv=sale.json['invoice_id']
  self.assertEqual(self.client.get('/invoices/'+str(inv)).status_code,200)
  pdf=self.client.get('/invoices/'+str(inv)+'/pdf')
  self.assertEqual(pdf.status_code,200)
  self.assertTrue(pdf.data.startswith(b'%PDF'))
  url='/invoices/'+str(inv)+'/refund'
  self.assertEqual(self.client.post(url,json={'amount':'10','reason':'Return'}).status_code,403)
  refund=self.client.post(url,json={'amount':'10','reason':'Return'},headers=headers)
  self.assertEqual(refund.status_code,200,refund.get_data(as_text=True))
  with get_db() as db:
   self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(self.pid,)).fetchone()[0],4)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM invoices WHERE business_id=?',(self.bid,)).fetchone()[0],1)
   self.assertEqual(float(db.execute('SELECT SUM(amount) FROM invoice_refunds WHERE invoice_id=?',(inv,)).fetchone()[0]),10)
 def test_invalid_csrf_rejected_for_json(self):
  r=self.client.post('/products/threshold',json={'threshold':3},headers={'X-CSRF-Token':'invalid'})
  self.assertEqual(r.status_code,403)
if __name__=='__main__':unittest.main()
