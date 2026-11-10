"""Regression coverage for invoice-linked customer returns."""
import os
import tempfile
import unittest
from unittest.mock import patch

class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'DATABASE_PATH':os.path.join(self.tmp.name,'returns.db')})
        self.env.start()
        from app import app,init_db,get_db
        app.config.update(TESTING=True)
        init_db()
        with get_db() as db:
            db.execute("INSERT INTO users(name,email,password) VALUES('Owner','returns@example.test','unused')")
            uid=db.execute("SELECT id FROM users WHERE email='returns@example.test'").fetchone()[0]
            db.execute("INSERT INTO businesses(name) VALUES('Returns Shop')")
            self.bid=db.execute("SELECT id FROM businesses WHERE name='Returns Shop'").fetchone()[0]
            db.execute("INSERT INTO business_memberships(business_id,user_id,role) VALUES (?,?,'owner')",(self.bid,uid))
            db.execute("INSERT INTO products(business_id,name,sku,category,price,stock) VALUES (?,?,?,?,?,?)",(self.bid,'Pen','RET-PEN','Office',10,7))
            self.pid=db.execute("SELECT id FROM products WHERE sku='RET-PEN'").fetchone()[0]
            db.execute("INSERT INTO invoices(invoice_number,customer_name,subtotal,tax,total,status,business_id) VALUES (?,?,?,?,?,?,?)",('RET-001','Customer',30,0,30,'paid',self.bid))
            self.inv=db.execute("SELECT id FROM invoices WHERE invoice_number='RET-001'").fetchone()[0]
            db.execute("INSERT INTO invoice_items(invoice_id,product_id,product_name,quantity,unit_price,total) VALUES (?,?,?,?,?,?)",(self.inv,self.pid,'Pen',3,10,30))
            self.item=db.execute("SELECT id FROM invoice_items WHERE invoice_id=?",(self.inv,)).fetchone()[0]
        self.client=app.test_client()
        with self.client.session_transaction() as sess:sess['user_id']=uid

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def payload(self,qty=1,restock=True,refund='10',key='return-unique-request-001'):
        return {'idempotency_key':key,'reason':'Customer preference','refund_amount':refund,
                'items':[{'invoice_item_id':self.item,'quantity':qty,'restock':restock}]}

    def test_partial_return_refund_replay_and_limits(self):
        from app import get_db
        url=f'/invoices/{self.inv}/returns'
        first=self.client.post(url,json=self.payload())
        self.assertEqual(first.status_code,201,first.get_data(as_text=True))
        self.assertEqual(self.client.post(url,json=self.payload()).json['replayed'],True)
        self.assertEqual(self.client.post(url,json=self.payload(qty=2)).status_code,409)
        self.assertEqual(self.client.post(url,json=self.payload(qty=3,key='return-unique-request-002')).status_code,409)
        second=self.client.post(url,json=self.payload(qty=2,restock=False,refund='20',key='return-unique-request-003'))
        self.assertEqual(second.status_code,201,second.get_data(as_text=True))
        with get_db() as db:
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(self.pid,)).fetchone()[0],8)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sales_returns').fetchone()[0],2)
            self.assertEqual(float(db.execute('SELECT SUM(amount) FROM invoice_refunds').fetchone()[0]),30.0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM stock_movements WHERE source='customer_return'").fetchone()[0],1)
        self.assertEqual(self.client.post(url,json=self.payload(qty=1,key='return-unique-request-004')).status_code,409)
        self.assertEqual(self.client.get('/returns').status_code,200)

    def test_business_isolation_and_no_refund_overflow(self):
        from app import get_db
        with get_db() as db:
            db.execute("INSERT INTO businesses(name) VALUES('Other Shop')")
            other=db.execute("SELECT id FROM businesses WHERE name='Other Shop'").fetchone()[0]
            db.execute("INSERT INTO invoices(invoice_number,customer_name,subtotal,tax,total,status,business_id) VALUES (?,?,?,?,?,?,?)",('RET-OTHER','Other',20,0,20,'paid',other))
            other_inv=db.execute("SELECT id FROM invoices WHERE invoice_number='RET-OTHER'").fetchone()[0]
        self.assertEqual(self.client.post(f'/invoices/{other_inv}/returns',json=self.payload()).status_code,404)
        self.assertEqual(self.client.post(f'/invoices/{self.inv}/returns',json=self.payload(refund='31')).status_code,409)
        self.assertEqual(self.client.get(f'/invoices/{self.inv}').status_code,200)

if __name__=='__main__':unittest.main()
