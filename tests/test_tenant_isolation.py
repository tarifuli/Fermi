"""Run with: python -m unittest discover -s tests"""
import os
import sys
import tempfile
from pathlib import Path
import unittest

class TenantIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.original_cwd = os.getcwd()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        os.chdir(cls.tempdir.name)
        os.environ['SECRET_KEY'] = 'test-only-secret'
        import app
        cls.app_module = app
        cls.client = app.app.test_client()
        with app.get_db() as db:
            db.execute("INSERT INTO users (name,email,password) VALUES ('A','a@example.com','unused')")
            db.execute("INSERT INTO users (name,email,password) VALUES ('B','b@example.com','unused')")
            db.execute("INSERT INTO businesses (name) VALUES ('A Store')")
            db.execute("INSERT INTO businesses (name) VALUES ('B Store')")
            db.execute("INSERT INTO business_memberships (business_id,user_id) VALUES (1,1)")
            db.execute("INSERT INTO business_memberships (business_id,user_id) VALUES (2,2)")
            db.execute("INSERT INTO products (name,sku,price,stock,business_id) VALUES ('Private A','private-a',100,5,1)")
            db.execute("INSERT INTO products (name,sku,price,stock,business_id) VALUES ('Private B','private-b',200,5,2)")
            db.execute("INSERT INTO invoices (invoice_number,customer_name,subtotal,tax,total,business_id) VALUES ('A-1','A Customer',100,5,105,1)")
            db.execute("INSERT INTO invoices (invoice_number,customer_name,subtotal,tax,total,business_id) VALUES ('B-1','B Customer',200,10,210,2)")

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.original_cwd)
        cls.tempdir.cleanup()

    def as_user(self, user_id):
        with self.client.session_transaction() as session:
            session['user_id'] = user_id

    def test_product_api_is_private(self):
        self.as_user(1)
        products = self.client.get('/api/products').get_json()
        names = {p['name'] for p in products}
        self.assertIn('Private A', names)
        self.assertNotIn('Private B', names)

    def test_other_business_invoice_pdf_is_denied(self):
        self.as_user(1)
        self.assertEqual(self.client.get('/invoices/26/pdf').status_code, 404)

    def test_other_business_product_cannot_be_deleted(self):
        self.as_user(1)
        response = self.client.post('/products/delete/10')
        self.assertFalse(response.get_json()['success'])
        with self.app_module.get_db() as db:
            self.assertIsNotNone(db.execute('SELECT id FROM products WHERE id=10').fetchone())

    def test_invoice_ignores_forged_totals_and_prices(self):
        self.as_user(1)
        response = self.client.post('/invoices/create', json={
            'invoice_number': 'FORGED-123', 'customer_name': 'A Customer',
            'subtotal': 1, 'tax': 0, 'total': 1,
            'items': [{'product_id': 9, 'product_name': 'Fake', 'quantity': 1,
                       'unit_price': 0.01, 'total': 0.01}]
        })
        self.assertTrue(response.get_json()['success'])
        with self.app_module.get_db() as db:
            invoice = db.execute('SELECT * FROM invoices WHERE id=?',
                                 (response.get_json()['invoice_id'],)).fetchone()
        self.assertEqual(invoice['subtotal'], 100)
        self.assertEqual(invoice['tax'], 5)
        self.assertEqual(invoice['total'], 105)

    def test_cross_business_invoice_product_rejected(self):
        self.as_user(1)
        response = self.client.post('/invoices/create', json={
            'customer_name': 'A Customer',
            'items': [{'product_id': 10, 'quantity': 1}]
        })
        self.assertEqual(response.status_code, 400)

    def test_no_business_cannot_create_product(self):
        with self.client.session_transaction() as session:
            session['user_id'] = 999
        response = self.client.post('/products/add', json={'name':'X','sku':'x','category':'x','price':1,'stock':1})
        self.assertEqual(response.status_code, 403)

if __name__ == '__main__':
    unittest.main()
