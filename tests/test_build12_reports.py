"""Build 12 CSV report regression checks."""
import csv
import io
import os
import tempfile
import unittest
from unittest.mock import patch

class ReportExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DATABASE_PATH': os.path.join(self.tmp.name, 'reports.db')})
        self.env.start()
        from app import app, init_db, get_db
        self.app = app
        self.previous = app.config.get('TESTING')
        app.config['TESTING'] = True
        init_db()
        with get_db() as db:
            for n in (1, 2):
                db.execute('INSERT INTO users(name,email,password) VALUES (?,?,?)', ('Owner', f'owner{n}@example.test', 'unused'))
                uid = db.execute('SELECT id FROM users WHERE email=?', (f'owner{n}@example.test',)).fetchone()[0]
                db.execute('INSERT INTO businesses(name) VALUES (?)', (f'Shop {n}',))
                bid = db.execute('SELECT id FROM businesses WHERE name=?', (f'Shop {n}',)).fetchone()[0]
                db.execute("INSERT INTO business_memberships(business_id,user_id,role) VALUES (?,?,'owner')", (bid,uid))
                db.execute('INSERT INTO products(business_id,name,sku,category,price,stock) VALUES (?,?,?,?,?,?)',
                           (bid, '=HYPERLINK("bad")' if n==1 else 'Other', f'SKU{n}', 'Test', 10, 3))
                if n == 1:
                    self.uid = uid
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.uid

    def tearDown(self):
        self.app.config['TESTING'] = self.previous
        self.env.stop()
        self.tmp.cleanup()

    def test_inventory_export_is_scoped_and_formula_safe(self):
        response = self.client.get('/reports/export?type=inventory')
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2], "'=HYPERLINK(\"bad\")")
        self.assertEqual(float(rows[1][-1]), 30.0)

    def test_analytics_inventory_insights_and_export_links(self):
        response = self.client.get('/analytics?days=30')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Inventory intelligence', html)
        self.assertIn('Slow-moving inventory', html)
        self.assertIn('Download sales CSV', html)
        self.assertIn('Download refunds CSV', html)
        self.assertIn('Download inventory CSV', html)
        self.assertIn('30.00', html)
        self.assertNotIn('SKU2', html)

    def test_sales_refunds_export_and_invalid_dates(self):
        for kind in ('sales','refunds'):
            response = self.client.get('/reports/export?type='+kind)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(list(csv.reader(io.StringIO(response.get_data(as_text=True))))), 1)
        self.assertEqual(self.client.get('/reports/export?type=wrong').status_code, 400)
        self.assertEqual(self.client.get('/reports/export?start=2026-02-30').status_code, 400)
        self.assertEqual(self.client.get('/reports/export?start=2026-12-01&end=2026-01-01').status_code, 400)

if __name__ == '__main__':
    unittest.main()
