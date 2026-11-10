"""Invoice-linked customer returns, optional refunds, and inventory reconciliation."""
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from functools import wraps
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

returns_blueprint = Blueprint('returns', __name__)

def db_dependencies():
    from app import get_db, require_business, record_stock_movement
    return get_db, require_business, record_stock_movement

def authenticated(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapped

def failure(message, status=400):
    return jsonify({'success': False, 'error': message}), status

@returns_blueprint.route('/returns')
@authenticated
def returns_page():
    get_db, require_business, _ = db_dependencies()
    bid, error = require_business()
    if error:
        return error
    with get_db() as db:
        records = db.execute("""SELECT r.*,i.invoice_number,i.customer_name,
          (SELECT COALESCE(SUM(ri.quantity),0) FROM sales_return_items ri WHERE ri.return_id=r.id) AS item_count
          FROM sales_returns r JOIN invoices i ON i.id=r.invoice_id AND i.business_id=r.business_id
          WHERE r.business_id=? ORDER BY r.created_at DESC,r.id DESC""", (bid,)).fetchall()
    return render_template('returns.html', returns=records)

@returns_blueprint.route('/invoices/<int:invoice_id>/returns', methods=['POST'])
@authenticated
def create_return(invoice_id):
    get_db, require_business, record_movement = db_dependencies()
    bid, error = require_business()
    if error:
        return error
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return failure('Invalid return request.')
    key = data.get('idempotency_key')
    if not isinstance(key, str) or re.fullmatch(r'[A-Za-z0-9_-]{16,100}', key) is None:
        return failure('A unique return request key is required.')
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason.strip()) > 500:
        return failure('Invalid return reason.')
    raw_refund = data.get('refund_amount', '0')
    if isinstance(raw_refund, bool) or not isinstance(raw_refund, (str, int, float)):
        return failure('Invalid refund amount.')
    try:
        refund = Decimal(str(raw_refund))
        if not refund.is_finite() or refund < 0 or refund > 100000000 or refund.as_tuple().exponent < -2:
            raise ValueError()
    except (InvalidOperation, ValueError):
        return failure('Refund must be a nonnegative amount with at most two decimals.')
    lines = data.get('items')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        return failure('Select at least one returned item.')
    selected = {}
    for line in lines:
        if not isinstance(line, dict):
            return failure('Invalid returned item.')
        iid, qty, restock = line.get('invoice_item_id'), line.get('quantity'), line.get('restock')
        if type(iid) is not int or iid <= 0 or iid in selected or type(qty) is not int or not 1 <= qty <= 100000 or type(restock) is not bool:
            return failure('Invalid or duplicate returned item.')
        selected[iid] = (qty, restock)
    canonical = [(iid, *selected[iid]) for iid in sorted(selected)]
    digest = hashlib.sha256(json.dumps({'invoice_id':invoice_id,'items':canonical,'refund':str(refund),'reason':reason.strip()},sort_keys=True).encode()).hexdigest()
    with get_db() as db:
        query = 'SELECT id,total,status FROM invoices WHERE id=? AND business_id=?'
        if os.environ.get('DATABASE_URL'):
            query += ' FOR UPDATE'
        invoice = db.execute(query, (invoice_id,bid)).fetchone()
        if invoice is None:
            return failure('Invoice not found.',404)
        prior = db.execute('SELECT id,request_hash FROM sales_returns WHERE business_id=? AND request_key=?', (bid,key)).fetchone()
        if prior:
            if prior['request_hash'] != digest:
                return failure('This return key was used for different details.',409)
            return jsonify({'success':True,'replayed':True,'return_id':prior['id']})
        if invoice['status'] != 'paid':
            return failure('Only paid invoices can be returned.',409)
        rows = db.execute('SELECT id,product_id,product_name,quantity FROM invoice_items WHERE invoice_id=?', (invoice_id,)).fetchall()
        items = {row['id']:row for row in rows}
        for iid, qty, restock in canonical:
            item = items.get(iid)
            if item is None:
                return failure('Item does not belong to this invoice.',400)
            already = db.execute("""SELECT COALESCE(SUM(ri.quantity),0) FROM sales_return_items ri
              JOIN sales_returns r ON r.id=ri.return_id
              WHERE r.business_id=? AND r.invoice_id=? AND ri.invoice_item_id=?""", (bid,invoice_id,iid)).fetchone()[0]
            if int(already)+qty > item['quantity']:
                return failure('Return exceeds remaining quantity.',409)
            if restock:
                if item['product_id'] is None:
                    return failure('Custom invoice items cannot be restocked.',400)
                product = db.execute('SELECT stock FROM products WHERE id=? AND business_id=?', (item['product_id'],bid)).fetchone()
                if product is None or product['stock']+qty > 100000000:
                    return failure('Product unavailable or stock limit reached.',409)
        already_refunded = db.execute('SELECT COALESCE(SUM(amount),0) FROM invoice_refunds WHERE invoice_id=? AND business_id=?', (invoice_id,bid)).fetchone()[0]
        if refund > Decimal(str(invoice['total']))-Decimal(str(already_refunded)):
            return failure('Refund exceeds remaining paid amount.',409)
        ret = db.execute("""INSERT INTO sales_returns (business_id,invoice_id,request_key,request_hash,reason,refund_amount)
          VALUES (?,?,?,?,?,?) RETURNING id""", (bid,invoice_id,key,digest,reason.strip(),str(refund))).fetchone()
        rid = ret['id']
        for iid, qty, restock in canonical:
            item = items[iid]
            db.execute('INSERT INTO sales_return_items (return_id,invoice_item_id,quantity,restock) VALUES (?,?,?,?)',
                       (rid,iid,qty,restock))
            if restock:
                db.execute('UPDATE products SET stock=stock+? WHERE id=? AND business_id=?', (qty,item['product_id'],bid))
                after = db.execute('SELECT stock FROM products WHERE id=? AND business_id=?', (item['product_id'],bid)).fetchone()['stock']
                record_movement(db,bid,item['product_id'],item['product_name'],after-qty,after,
                                'Customer return #'+str(rid),'customer_return',rid)
        if refund > 0:
            db.execute('INSERT INTO invoice_refunds (business_id,invoice_id,amount,reason) VALUES (?,?,?,?)',
                       (bid,invoice_id,str(refund),reason.strip() or 'Customer return #'+str(rid)))
    return jsonify({'success':True,'replayed':False,'return_id':rid}),201
