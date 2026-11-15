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

    def test_custom_dates_and_comparison(self):
        from datetime import date, timedelta
        today = date.today()
        start = (today - timedelta(days=4)).isoformat()
        end = today.isoformat()
        response = self.client.get('/analytics?start='+start+'&end='+end)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Period comparison', html)
        self.assertIn('name="start"', html)
        self.assertIn('start='+start, html)
        self.assertIn('end='+end, html)
        self.assertEqual(self.client.get('/analytics?start=2026-02-30&end='+end).status_code, 400)
        self.assertEqual(self.client.get('/analytics?start='+end).status_code, 400)
        self.assertEqual(self.client.get('/analytics?start='+end+'&end='+start).status_code, 400)

    def test_build13_replenishment_scoped_and_read_only(self):
        from app import get_db
        with get_db() as db:
            before = db.execute('SELECT stock FROM products WHERE sku=?', ('SKU1',)).fetchone()[0]
        response = self.client.get('/inventory/replenishment?days=30')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['planning_horizon_days'], 14)
        self.assertEqual(len(payload['recommendations']), 1)
        item = payload['recommendations'][0]
        self.assertEqual(item['sku'], 'SKU1')
        self.assertEqual(item['priority'], 'low_stock')
        self.assertEqual(item['suggested_order_quantity'], 18)
        self.assertEqual(self.client.get('/inventory/replenishment?days=10').status_code, 400)
        with get_db() as db:
            after = db.execute('SELECT stock FROM products WHERE sku=?', ('SKU1',)).fetchone()[0]
        self.assertEqual(before, after)

    def test_build13_planner_page_and_supplier_isolation(self):
        from app import get_db
        page = self.client.get('/inventory/replenishment/plan')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Stock replenishment planner', html)
        self.assertIn('Add a supplier', html)
        self.assertIn('No supplier is registered yet', html)
        with get_db() as db:
            business = db.execute('SELECT business_id FROM business_memberships WHERE user_id=?',
                                  (self.uid,)).fetchone()['business_id']
            other = db.execute('SELECT id FROM businesses WHERE name=?', ('Shop 2',)).fetchone()['id']
            db.execute('INSERT INTO suppliers (business_id,name) VALUES (?,?)', (other, 'Other Supplier'))
            db.execute('INSERT INTO suppliers (business_id,name) VALUES (?,?)', (business, 'My Supplier'))
        page = self.client.get('/inventory/replenishment/plan')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('My Supplier', html)
        self.assertNotIn('Other Supplier', html)
        self.assertIn('Create draft from selected products', html)
        self.assertIn('/purchase-orders', html)
        self.assertIn('Plan replenishment', self.client.get('/products').get_data(as_text=True))

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
