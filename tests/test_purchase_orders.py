"""Purchase order and supplier integration regression tests (SQLite)."""
import os
import tempfile
import unittest
from unittest.mock import patch


class PurchasingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DATABASE_PATH': os.path.join(self.tmp.name, 'po.db')}, clear=False)
        self.env.start()
        from app import app, init_db, get_db
        self.app = app
        self.app.config.update(TESTING=True)
        init_db()
        with get_db() as db:
            db.execute('INSERT INTO users (name,email,password) VALUES (?,?,?)', ('Owner','po@test.local','unused'))
            self.user = db.execute('SELECT id FROM users WHERE email=?', ('po@test.local',)).fetchone()[0]
            db.execute('INSERT INTO businesses (name) VALUES (?)', ('Purchasing Shop',))
            self.business = db.execute('SELECT id FROM businesses WHERE name=?', ('Purchasing Shop',)).fetchone()[0]
            db.execute('INSERT INTO business_memberships (business_id,user_id,role) VALUES (?,?,?)', (self.business,self.user,'owner'))
            db.execute('INSERT INTO products (business_id,name,sku,category,price,stock) VALUES (?,?,?,?,?,?)',
                       (self.business,'Notebook','PO-NB','Office',10,19))
            self.product = db.execute('SELECT id FROM products WHERE sku=?', ('PO-NB',)).fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = self.user

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def create_order(self, quantity=5):
        supplier = self.client.post('/suppliers',json={'name':'Stationery Wholesale'})
        self.assertEqual(supplier.status_code,201,supplier.get_data(as_text=True))
        order = self.client.post('/purchase-orders',json={'supplier_id':supplier.json['supplier_id'],
            'items':[{'product_id':self.product,'quantity':quantity,'unit_cost':'8.50'}]})
        self.assertEqual(order.status_code,201,order.get_data(as_text=True))
        return order.json['order_id']

    def get_line(self, oid):
        from app import get_db
        with get_db() as db:
            return db.execute('SELECT id FROM purchase_order_items WHERE purchase_order_id=?',(oid,)).fetchone()[0]

    def test_draft_does_not_restock_and_receipts_are_idempotent(self):
        from app import get_db
        oid=self.create_order()
        item=self.get_line(oid)
        key='receipt-key-unique-000001'
        payload={'idempotency_key':key,'items':[{'po_item_id':item,'quantity':2}]}
        self.assertEqual(self.client.post('/purchase-orders/%d/receive'%oid,json=payload).status_code,409)
        self.assertEqual(self.client.post('/purchase-orders/%d/order'%oid).status_code,200)
        first=self.client.post('/purchase-orders/%d/receive'%oid,json=payload)
        self.assertEqual(first.status_code,200,first.get_data(as_text=True))
        self.assertEqual(first.json['status'],'partially_received')
        replay=self.client.post('/purchase-orders/%d/receive'%oid,json=payload)
        self.assertEqual(replay.status_code,200)
        self.assertTrue(replay.json['replayed'])
        changed=dict(payload)
        changed['items']=[{'po_item_id':item,'quantity':3}]
        self.assertEqual(self.client.post('/purchase-orders/%d/receive'%oid,json=changed).status_code,409)
        finish=self.client.post('/purchase-orders/%d/receive'%oid,json={
            'idempotency_key':'receipt-key-unique-000002',
            'items':[{'po_item_id':item,'quantity':3}]})
        self.assertEqual(finish.status_code,200)
        self.assertEqual(finish.json['status'],'received')
        with get_db() as db:
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(self.product,)).fetchone()[0],24)
            rows=db.execute("SELECT quantity_change FROM stock_movements WHERE source='purchase_order' AND business_id=?",(self.business,)).fetchall()
            self.assertEqual([row[0] for row in rows],[2,3])
        self.assertIn(b'Receiving history',self.client.get('/purchase-orders/%d'%oid).data)

    def test_invalid_receipts_and_supplier_business_isolation(self):
        from app import get_db
        oid=self.create_order()
        item=self.get_line(oid)
        self.client.post('/purchase-orders/%d/order'%oid)
        for quantity in (0,-1,1.5,True,'2',6):
            response=self.client.post('/purchase-orders/%d/receive'%oid,json={
                'idempotency_key':'receipt-key-invalid-0001',
                'items':[{'po_item_id':item,'quantity':quantity}]})
            self.assertIn(response.status_code,(400,409))
        with get_db() as db:
            db.execute('INSERT INTO businesses (name) VALUES (?)',('Other Shop',))
            other=db.execute('SELECT id FROM businesses WHERE name=?',('Other Shop',)).fetchone()[0]
            db.execute('INSERT INTO suppliers (business_id,name) VALUES (?,?)',(other,'Private Supplier'))
            other_supplier=db.execute('SELECT id FROM suppliers WHERE business_id=?',(other,)).fetchone()[0]
        cross=self.client.post('/purchase-orders',json={'supplier_id':other_supplier,
            'items':[{'product_id':self.product,'quantity':1,'unit_cost':'2.00'}]})
        self.assertEqual(cross.status_code,404)
        self.assertEqual(self.client.get('/purchase-orders/999999').status_code,404)
        with get_db() as db:
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(self.product,)).fetchone()[0],19)

    def test_supplier_and_purchase_order_pages(self):
        self.assertEqual(self.client.get('/suppliers').status_code,200)
        self.assertEqual(self.client.get('/purchase-orders').status_code,200)
        oid=self.create_order()
        self.assertIn(b'Stationery Wholesale',self.client.get('/purchase-orders').data)
        self.assertEqual(self.client.get('/purchase-orders/%d'%oid).status_code,200)


    def test_supplier_edit_is_scoped_and_preserves_order_history(self):
        from app import get_db
        oid = self.create_order()
        with get_db() as db:
            sid = db.execute('SELECT supplier_id FROM purchase_orders WHERE id=?', (oid,)).fetchone()[0]
            db.execute('INSERT INTO businesses (name) VALUES (?)', ('Second Business',))
            other = db.execute('SELECT id FROM businesses WHERE name=?', ('Second Business',)).fetchone()[0]
            db.execute('INSERT INTO suppliers (business_id,name) VALUES (?,?)', (other, 'Other Supplier'))
            other_sid = db.execute('SELECT id FROM suppliers WHERE business_id=?', (other,)).fetchone()[0]
        payload = {'name':'Updated Wholesale','contact_name':'Manager','email':'hello@example.test','phone':'123'}
        self.assertEqual(self.client.patch('/suppliers/%d'%other_sid, json=payload).status_code,404)
        response = self.client.patch('/suppliers/%d'%sid, json=payload)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertIn(b'Updated Wholesale',self.client.get('/suppliers').data)
        self.assertIn(b'Updated Wholesale',self.client.get('/purchase-orders/%d'%oid).data)
        self.assertEqual(self.client.patch('/suppliers/%d'%sid,json={**payload,'name':''}).status_code,400)

    def test_receipt_stock_limit_is_atomic(self):
        from app import get_db
        oid=self.create_order()
        item=self.get_line(oid)
        self.client.post('/purchase-orders/%d/order'%oid)
        with get_db() as db:
            db.execute('UPDATE products SET stock=? WHERE id=?', (100000000,self.product))
        response=self.client.post('/purchase-orders/%d/receive'%oid,json={
            'idempotency_key':'receipt-stock-limit-00001',
            'items':[{'po_item_id':item,'quantity':1}]})
        self.assertEqual(response.status_code,409,response.get_data(as_text=True))
        with get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchase_order_receipts WHERE purchase_order_id=?',(oid,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT quantity_received FROM purchase_order_items WHERE id=?',(item,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',(self.product,)).fetchone()[0],100000000)


if __name__=='__main__':
    unittest.main()
