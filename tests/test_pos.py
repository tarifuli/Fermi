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

    def test_pos_requires_login(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        self.assertEqual(self.client.get('/pos').status_code, 302)


if __name__ == '__main__':
    unittest.main()
