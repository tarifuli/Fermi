"""POS regression tests using isolated SQLite data; no external services required."""
import os
import tempfile
import unittest
from unittest.mock import patch


class POSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, 'test.db')
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db_path}, clear=False)
        self.env.start()
        from app import app, init_db, get_db
        self.app = app
        self.app.config.update(TESTING=True)
        init_db()
        with get_db() as db:
            db.execute("INSERT INTO users (name,email,password) VALUES (?,?,?)", ('Cashier', 'pos@example.test', 'unused'))
            user_id = db.execute("SELECT id FROM users WHERE email=?", ('pos@example.test',)).fetchone()[0]
            db.execute("INSERT INTO businesses (name) VALUES (?)", ('POS Shop',))
            business_id = db.execute("SELECT id FROM businesses WHERE name=?", ('POS Shop',)).fetchone()[0]
            db.execute("INSERT INTO business_memberships (business_id,user_id,role) VALUES (?,?,?)", (business_id,user_id,'owner'))
            db.execute("INSERT INTO products (business_id,name,sku,category,price,stock) VALUES (?,?,?,?,?,?)",
                       (business_id,'Notebook','POS-NB','Stationery',100,3))
            self.product_id = db.execute("SELECT id FROM products WHERE sku=?", ('POS-NB',)).fetchone()[0]
            self.business_id = business_id
            self.user_id = user_id
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.user_id

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_duplicate_product_lines_cannot_oversell(self):
        from app import get_db
        response = self.client.post('/invoices/create', json={
            'customer_name': 'Duplicate Lines',
            'items': [
                {'product_id': self.product_id, 'quantity': 2},
                {'product_id': self.product_id, 'quantity': 2}
            ]
        })
        self.assertEqual(response.status_code, 400)
        with get_db() as db:
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',
                                        (self.product_id,)).fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM invoices WHERE business_id=?',
                                        (self.business_id,)).fetchone()[0], 0)

    def test_invoice_limits_and_notes_validation(self):
        for items, notes in [
            ([{'product_id': self.product_id, 'quantity': 100001}], ''),
            ([{'product_id': self.product_id, 'quantity': 1}] * 101, ''),
            ([{'product_id': self.product_id, 'quantity': 1}], 'a' * 5001)
        ]:
            response = self.client.post('/invoices/create', json={
                'customer_name': 'Invalid Sale', 'items': items, 'notes': notes
            })
            self.assertEqual(response.status_code, 400)

    def test_idempotent_checkout_returns_original_invoice(self):
        from app import get_db
        payload = {'idempotency_key': 'checkout_test_key_0001',
                   'customer_name': 'Repeat Safe', 'status': 'paid',
                   'items': [{'product_id': self.product_id, 'quantity': 2}]}
        first = self.client.post('/invoices/create', json=payload)
        second = self.client.post('/invoices/create', json=payload)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        self.assertEqual(first.json['invoice_id'], second.json['invoice_id'])
        self.assertTrue(second.json['replayed'])
        with get_db() as db:
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?',
                                        (self.product_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM invoices WHERE business_id=?',
                                        (self.business_id,)).fetchone()[0], 1)
        changed = dict(payload)
        changed['customer_name'] = 'Different Buyer'
        conflict = self.client.post('/invoices/create', json=changed)
        self.assertEqual(conflict.status_code, 409)

    def test_invalid_idempotency_key_is_rejected(self):
        response = self.client.post('/invoices/create', json={
            'idempotency_key': 'short', 'customer_name': 'Invalid Key',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(response.status_code, 400)

    def test_inventory_threshold_and_movement_history(self):
        from app import get_db
        saved = self.client.post('/products/threshold', json={'threshold': 2})
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertIn(b'Low stock (1', self.client.get('/products').data)
        self.assertEqual(self.client.post('/products/threshold', json={'threshold': -1}).status_code, 400)
        adjusted = self.client.post('/products/adjust/{}'.format(self.product_id),
                                    json={'delta': 2, 'reason': 'New shipment'})
        self.assertEqual(adjusted.status_code, 200)
        sale = self.client.post('/invoices/create', json={
            'customer_name': 'Inventory Audit',
            'items': [{'product_id': self.product_id, 'quantity': 1}]})
        self.assertEqual(sale.status_code, 200, sale.get_data(as_text=True))
        history = self.client.get('/products/history')
        self.assertEqual(history.status_code, 200)
        self.assertIn(b'New shipment', history.data)
        self.assertIn(b'Invoice sale', history.data)
        with get_db() as db:
            rows = db.execute('SELECT quantity_change FROM stock_movements WHERE business_id=? ORDER BY id',
                              (self.business_id,)).fetchall()
            self.assertEqual([row[0] for row in rows], [2, -1])

    def test_pos_page_and_checkout_updates_stock(self):
        from app import get_db
        page = self.client.get('/pos')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Notebook', page.data)
        response = self.client.post('/invoices/create', json={
            'customer_name': 'Walk-in Customer',
            'status': 'paid',
            'items': [{'product_id': self.product_id, 'quantity': 2}]
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.json['success'])
        with get_db() as db:
            self.assertEqual(db.execute("SELECT stock FROM products WHERE id=?", (self.product_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM invoices WHERE business_id=?", (self.business_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM invoice_items WHERE invoice_id=?", (response.json['invoice_id'],)).fetchone()[0], 1)

    def test_insufficient_stock_does_not_create_invoice(self):
        from app import get_db
        response = self.client.post('/invoices/create', json={
            'customer_name': 'Walk-in Customer',
            'items': [{'product_id': self.product_id, 'quantity': 4}]
        })
        self.assertEqual(response.status_code, 400)
        with get_db() as db:
            self.assertEqual(db.execute("SELECT stock FROM products WHERE id=?", (self.product_id,)).fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM invoices WHERE business_id=?", (self.business_id,)).fetchone()[0], 0)

    def test_pdf_matches_invoice_data_and_is_downloadable(self):
        response = self.client.post('/invoices/create', json={
            'customer_name': 'online order',
            'status': 'paid',
            'notes': 'Created from Fermi POS',
            'items': [{'product_id': self.product_id, 'quantity': 2}]
        })
        self.assertEqual(response.status_code, 200)
        pdf = self.client.get('/invoices/{}/pdf'.format(response.json['invoice_id']))
        self.assertEqual(pdf.status_code, 200, pdf.get_data(as_text=True)[:300] if pdf.status_code != 200 else '')
        self.assertEqual(pdf.mimetype, 'application/pdf')
        self.assertTrue(pdf.data.startswith(b'%PDF'))
        self.assertGreater(len(pdf.data), 1800)

    def test_stock_adjustment_and_nonnegative_guard(self):
        from app import get_db
        success = self.client.post('/products/adjust/{}'.format(self.product_id), json={'delta': 5})
        self.assertEqual(success.status_code, 200)
        self.assertEqual(success.json['stock'], 8)
        rejected = self.client.post('/products/adjust/{}'.format(self.product_id), json={'delta': -9})
        self.assertEqual(rejected.status_code, 400)
        with get_db() as db:
            self.assertEqual(db.execute('SELECT stock FROM products WHERE id=?', (self.product_id,)).fetchone()[0], 8)

    def test_stock_adjustment_rejects_invalid_input(self):
        for delta in [0, 1.5, True, 'abc', 100001]:
            response = self.client.post('/products/adjust/{}'.format(self.product_id), json={'delta': delta})
            self.assertEqual(response.status_code, 400, str(delta))

    def test_sales_analytics_is_business_scoped(self):
        from app import get_db
        sale = self.client.post('/invoices/create', json={
            'customer_name': 'Analytics Test', 'status': 'paid',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(sale.status_code, 200)
        report = self.client.get('/analytics?days=30')
        self.assertEqual(report.status_code, 200)
        self.assertIn(b'Analytics Test' if False else b'Paid revenue', report.data)
        self.assertIn(b'Notebook', report.data)
        with get_db() as db:
            db.execute("INSERT INTO businesses (name) VALUES (?)", ('Other Shop',))
            other_id = db.execute("SELECT id FROM businesses WHERE name=?", ('Other Shop',)).fetchone()[0]
            db.execute("""INSERT INTO invoices
                (invoice_number,customer_name,subtotal,tax,total,status,business_id)
                VALUES (?,?,?,?,?,?,?)""",
                ('OTHER-100','Secret Buyer',999,49.95,1048.95,'paid',other_id))
        isolated = self.client.get('/analytics?days=30')
        self.assertNotIn(b'Secret Buyer', isolated.data)
        self.assertNotIn(b'1,048.95', isolated.data)

    def test_analytics_period_validation_and_authentication(self):
        self.assertEqual(self.client.get('/analytics?days=invalid').status_code, 200)
        for days in (7, 30, 90):
            self.assertEqual(self.client.get('/analytics?days='+str(days)).status_code, 200)
        with self.client.session_transaction() as sess:
            sess.clear()
        self.assertEqual(self.client.get('/analytics').status_code, 302)

    def test_customer_insights_and_purchase_history(self):
        first = self.client.post('/invoices/create', json={
            'customer_name': 'Repeat Buyer', 'status': 'paid',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        second = self.client.post('/invoices/create', json={
            'customer_name': 'Repeat Buyer', 'status': 'pending',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        directory = self.client.get('/customers')
        self.assertEqual(directory.status_code, 200)
        self.assertIn(b'Returning customers', directory.data)
        history = self.client.get('/customers/history?name=repeat%20buyer')
        self.assertEqual(history.status_code, 200)
        self.assertIn(b'Frequently purchased products', history.data)
        self.assertIn(b'Notebook', history.data)
        self.assertIn(b'Pending balance', history.data)
        self.assertIn(b'Save as PDF', history.data)

    def test_editable_customer_profile_preserves_invoice(self):
        from app import get_db
        sale = self.client.post('/invoices/create', json={
            'customer_name': 'Original Buyer', 'status': 'paid',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(sale.status_code, 200)
        saved = self.client.post('/customers/profile?name=original%20buyer', data={
            'display_name': 'Preferred Customer', 'email': 'customer@example.test',
            'phone': '01700000000', 'notes': 'Prefers afternoon delivery'
        }, follow_redirects=True)
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b'Preferred Customer', saved.data)
        self.assertIn(b'Prefers afternoon delivery', saved.data)
        with get_db() as db:
            invoice = db.execute('SELECT customer_name FROM invoices WHERE id=?',
                                 (sale.json['invoice_id'],)).fetchone()
            self.assertEqual(invoice['customer_name'], 'Original Buyer')
        self.assertEqual(self.client.get('/customers/profile?name=unknown').status_code, 302)

    def test_customer_lookup_and_saved_profile_checkout(self):
        from app import get_db
        original = self.client.post('/invoices/create', json={
            'customer_name': 'Original Buyer', 'status': 'paid',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(original.status_code, 200)
        self.client.post('/customers/profile?name=original%20buyer', data={
            'display_name': 'VIP Buyer', 'email': 'vip@example.test',
            'phone': '01711111111', 'notes': 'Frequent buyer'
        })
        lookup = self.client.get('/api/customers/lookup')
        self.assertEqual(lookup.status_code, 200)
        self.assertIn('VIP Buyer', str(lookup.json))
        self.assertNotIn('Frequent buyer', str(lookup.json))
        sale = self.client.post('/invoices/create', json={
            'customer_key': 'original buyer', 'customer_name': 'Ignored Input',
            'customer_email': 'spoof@example.test', 'status': 'paid',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(sale.status_code, 200)
        with get_db() as db:
            inv = db.execute('SELECT customer_name,customer_email,customer_phone FROM invoices WHERE id=?',
                             (sale.json['invoice_id'],)).fetchone()
            self.assertEqual(inv['customer_name'], 'VIP Buyer')
            self.assertEqual(inv['customer_email'], 'vip@example.test')
            self.assertEqual(inv['customer_phone'], '01711111111')
        invalid = self.client.post('/invoices/create', json={
            'customer_key': 'nonexistent', 'customer_name': 'Bad',
            'items': [{'product_id': self.product_id, 'quantity': 1}]
        })
        self.assertEqual(invalid.status_code, 400)

    def test_pos_requires_login(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        self.assertEqual(self.client.get('/pos').status_code, 302)


if __name__ == '__main__':
    unittest.main()
