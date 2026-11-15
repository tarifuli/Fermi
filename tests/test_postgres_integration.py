"""PostgreSQL integration smoke test; requires DATABASE_URL and an initialized schema."""
import os
import unittest
import uuid
from db_backend import get_db


@unittest.skipUnless(os.environ.get('DATABASE_URL'), 'PostgreSQL DATABASE_URL not configured')
class PostgresIntegrationTest(unittest.TestCase):
    def test_transactions_and_tenant_scoped_products(self):
        unique = uuid.uuid4().hex
        with get_db() as db:
            first = db.execute('INSERT INTO businesses (name) VALUES (?) RETURNING id', ('Test A '+unique,)).fetchone()['id']
            second = db.execute('INSERT INTO businesses (name) VALUES (?) RETURNING id', ('Test B '+unique,)).fetchone()['id']
            db.execute('INSERT INTO products (business_id,name,sku,price,stock) VALUES (?,?,?,?,?)',
                       (first, 'Test product', 'TEST-'+unique, 25.50, 4))
            db.execute('INSERT INTO products (business_id,name,sku,price,stock) VALUES (?,?,?,?,?)',
                       (second, 'Test product', 'TEST-'+unique, 25.50, 4))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM products WHERE business_id=?', (first,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM products WHERE business_id=?', (second,)).fetchone()[0], 1)
            self.assertEqual(db.execute('UPDATE products SET stock=stock-? WHERE business_id=? AND stock>=?', (3,first,3)).rowcount, 1)
            self.assertEqual(db.execute('SELECT stock FROM products WHERE business_id=?', (second,)).fetchone()['stock'], 4)
            db.execute('DELETE FROM products WHERE business_id IN (?,?)', (first,second))
            db.execute('DELETE FROM businesses WHERE id IN (?,?)', (first,second))

    def test_migration_011_invoice_columns(self):
        """Build 15/17 schema gate: discounts and payment methods must exist."""
        unique = uuid.uuid4().hex
        with get_db() as db:
            bid = db.execute('INSERT INTO businesses(name) VALUES (?) RETURNING id',
                             ('Migration gate ' + unique,)).fetchone()['id']
            try:
                inv = db.execute(
                    """INSERT INTO invoices(business_id,invoice_number,customer_name,
                       subtotal,tax,total,status,discount,payment_method)
                       VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
                    (bid, 'GATE-' + unique, 'CI', 100, 4, 84, 'paid', 20, 'mobile_banking')
                ).fetchone()['id']
                row = db.execute('SELECT discount,payment_method,total FROM invoices WHERE id=?',
                                 (inv,)).fetchone()
                self.assertEqual(float(row['discount']), 20.0)
                self.assertEqual(row['payment_method'], 'mobile_banking')
                self.assertEqual(float(row['total']), 84.0)
            finally:
                db.execute('DELETE FROM invoices WHERE business_id=?', (bid,))
                db.execute('DELETE FROM businesses WHERE id=?', (bid,))

    def test_rollback(self):
        unique = 'Rollback '+uuid.uuid4().hex
        with self.assertRaises(ValueError):
            with get_db() as db:
                db.execute('INSERT INTO businesses (name) VALUES (?)', (unique,))
                raise ValueError('rollback')
        with get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM businesses WHERE name=?', (unique,)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
