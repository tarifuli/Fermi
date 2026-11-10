"""Supplier, purchase order and idempotent inventory receipt workflows."""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from flask import Blueprint, jsonify, render_template, request, redirect, url_for

po_blueprint = Blueprint('purchasing', __name__)


def dependencies():
    from app import get_db, require_business, record_stock_movement
    return get_db, require_business, record_stock_movement


def business_or_error():
    _, require_business, _ = dependencies()
    return require_business()


def logged_in(view):
    from functools import wraps
    from flask import session
    @wraps(view)
    def protected(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return protected


def bad(message, status=400):
    return jsonify({'success': False, 'error': message}), status


def text_field(data, key, maximum, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError('Invalid ' + key.replace('_', ' '))
    return value.strip()


def parse_lines(lines):
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise ValueError('Provide 1–100 purchase order lines.')
    seen = set()
    output = []
    for item in lines:
        if not isinstance(item, dict):
            raise ValueError('Invalid purchase order item.')
        pid, quantity = item.get('product_id'), item.get('quantity')
        if type(pid) is not int or pid <= 0 or pid in seen:
            raise ValueError('Each product must appear once.')
        if type(quantity) is not int or not 1 <= quantity <= 100000:
            raise ValueError('Quantity must be 1–100,000.')
        raw = item.get('unit_cost')
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ValueError('Invalid purchase cost.')
        try:
            cost = Decimal(str(raw))
            if not cost.is_finite() or cost < 0 or cost > 100000000 or cost.as_tuple().exponent < -2:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise ValueError('Purchase cost must be nonnegative with at most two decimals.')
        seen.add(pid)
        output.append((pid, quantity, str(cost)))
    return output


@po_blueprint.route('/suppliers', methods=['GET'])
@logged_in
def suppliers_page():
    bid, error = business_or_error()
    if error:
        return error
    get_db, _, _ = dependencies()
    with get_db() as db:
        suppliers = db.execute('''SELECT s.*, COUNT(p.id) AS po_count
            FROM suppliers s LEFT JOIN purchase_orders p ON p.supplier_id=s.id AND p.business_id=s.business_id
            WHERE s.business_id=? GROUP BY s.id ORDER BY s.name''', (bid,)).fetchall()
    return render_template('suppliers.html', suppliers=suppliers)


@po_blueprint.route('/suppliers', methods=['POST'])
@logged_in
def create_supplier():
    bid, error = business_or_error()
    if error:
        return error
    d = request.get_json(silent=True) or {}
    try:
        name = text_field(d, 'name', 150, True)
        contact = text_field(d, 'contact_name', 150)
        email = text_field(d, 'email', 200)
        phone = text_field(d, 'phone', 50)
    except ValueError as e:
        return bad(str(e))
    get_db, _, _ = dependencies()
    with get_db() as db:
        result = db.execute('''INSERT INTO suppliers (business_id,name,contact_name,email,phone)
            VALUES (?,?,?,?,?) RETURNING id''', (bid,name,contact,email,phone)).fetchone()
    return jsonify({'success': True, 'supplier_id': result['id']}), 201



@po_blueprint.route('/suppliers/<int:supplier_id>', methods=['PATCH'])
@logged_in
def update_supplier(supplier_id):
    bid, error = business_or_error()
    if error:
        return error
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return bad('Invalid supplier details.')
    try:
        name = text_field(d, 'name', 150, True)
        contact = text_field(d, 'contact_name', 150)
        email = text_field(d, 'email', 200)
        phone = text_field(d, 'phone', 50)
    except ValueError as e:
        return bad(str(e))
    get_db, _, _ = dependencies()
    with get_db() as db:
        result = db.execute('''UPDATE suppliers
            SET name=?,contact_name=?,email=?,phone=?
            WHERE id=? AND business_id=?''',
            (name, contact, email, phone, supplier_id, bid))
        if result.rowcount != 1:
            return bad('Supplier not found.', 404)
    return jsonify({'success': True, 'supplier_id': supplier_id})


@po_blueprint.route('/purchase-orders', methods=['GET'])
@logged_in
def orders_page():
    bid, error = business_or_error()
    if error:
        return error
    get_db, _, _ = dependencies()
    with get_db() as db:
        suppliers = db.execute('SELECT id,name FROM suppliers WHERE business_id=? ORDER BY name', (bid,)).fetchall()
        products = db.execute('SELECT id,name,sku,stock FROM products WHERE business_id=? ORDER BY name', (bid,)).fetchall()
        orders = db.execute('''SELECT p.*,s.name AS supplier_name,
            COALESCE((SELECT SUM(i.quantity_ordered*i.unit_cost) FROM purchase_order_items i
                      WHERE i.purchase_order_id=p.id AND i.business_id=p.business_id),0) AS total_cost
            FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id AND s.business_id=p.business_id
            WHERE p.business_id=? ORDER BY p.created_at DESC,p.id DESC''', (bid,)).fetchall()
    return render_template('purchase_orders.html', suppliers=suppliers, products=products, orders=orders)


@po_blueprint.route('/purchase-orders', methods=['POST'])
@logged_in
def create_order():
    bid, error = business_or_error()
    if error:
        return error
    d = request.get_json(silent=True) or {}
    supplier_id = d.get('supplier_id')
    if type(supplier_id) is not int or supplier_id <= 0:
        return bad('Choose a supplier.')
    try:
        notes = text_field(d, 'notes', 1000)
        lines = parse_lines(d.get('items'))
    except ValueError as e:
        return bad(str(e))
    get_db, _, _ = dependencies()
    with get_db() as db:
        if db.execute('SELECT id FROM suppliers WHERE id=? AND business_id=?', (supplier_id,bid)).fetchone() is None:
            return bad('Supplier not found.',404)
        for pid, _, _ in lines:
            if db.execute('SELECT id FROM products WHERE id=? AND business_id=?', (pid,bid)).fetchone() is None:
                return bad('One or more products are not in this business.',400)
        order = db.execute('''INSERT INTO purchase_orders (business_id,supplier_id,notes)
            VALUES (?,?,?) RETURNING id''', (bid,supplier_id,notes)).fetchone()
        oid = order['id']
        for pid, qty, cost in lines:
            db.execute('''INSERT INTO purchase_order_items
                (business_id,purchase_order_id,product_id,quantity_ordered,unit_cost)
                VALUES (?,?,?,?,?)''', (bid,oid,pid,qty,cost))
    return jsonify({'success': True, 'order_id': oid}), 201


@po_blueprint.route('/purchase-orders/<int:oid>')
@logged_in
def order_detail(oid):
    bid, error = business_or_error()
    if error:
        return error
    get_db, _, _ = dependencies()
    with get_db() as db:
        order = db.execute('''SELECT p.*,s.name AS supplier_name FROM purchase_orders p
            JOIN suppliers s ON s.id=p.supplier_id AND s.business_id=p.business_id
            WHERE p.id=? AND p.business_id=?''', (oid,bid)).fetchone()
        if order is None:
            return bad('Purchase order not found.',404)
        items = db.execute('''SELECT i.*,p.name AS product_name,p.sku
            FROM purchase_order_items i JOIN products p ON p.id=i.product_id AND p.business_id=i.business_id
            WHERE i.purchase_order_id=? AND i.business_id=? ORDER BY i.id''', (oid,bid)).fetchall()
        receipts = db.execute('''SELECT r.id,r.created_at,ri.quantity,p.name AS product_name
            FROM purchase_order_receipts r JOIN purchase_order_receipt_items ri ON ri.receipt_id=r.id
            JOIN purchase_order_items i ON i.id=ri.po_item_id
            JOIN products p ON p.id=i.product_id AND p.business_id=r.business_id
            WHERE r.purchase_order_id=? AND r.business_id=? ORDER BY r.id DESC''', (oid,bid)).fetchall()
    return render_template('purchase_order_detail.html', order=order, items=items, receipts=receipts)


@po_blueprint.route('/purchase-orders/<int:oid>/order', methods=['POST'])
@logged_in
def mark_ordered(oid):
    bid, error = business_or_error()
    if error:
        return error
    get_db, _, _ = dependencies()
    with get_db() as db:
        changed = db.execute('''UPDATE purchase_orders SET status='ordered',ordered_at=CURRENT_TIMESTAMP
            WHERE id=? AND business_id=? AND status='draft' ''', (oid,bid))
        if changed.rowcount != 1:
            return bad('Only a draft purchase order can be marked ordered.',409)
    return jsonify({'success': True, 'status': 'ordered'})


@po_blueprint.route('/purchase-orders/<int:oid>/receive', methods=['POST'])
@logged_in
def receive_order(oid):
    bid, error = business_or_error()
    if error:
        return error
    d = request.get_json(silent=True) or {}
    key = d.get('idempotency_key')
    if not isinstance(key,str) or re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key) is None:
        return bad('A valid unique receipt key is required.')
    lines = d.get('items')
    if not isinstance(lines,list) or not 1 <= len(lines) <= 100:
        return bad('Enter at least one receipt line.')
    quantities = {}
    for line in lines:
        if not isinstance(line,dict) or type(line.get('po_item_id')) is not int or type(line.get('quantity')) is not int:
            return bad('Invalid receipt line.')
        item_id, qty = line['po_item_id'], line['quantity']
        if item_id <= 0 or item_id in quantities or not 1 <= qty <= 100000:
            return bad('Duplicate or invalid receipt quantity.')
        quantities[item_id] = qty
    canonical = sorted(quantities.items())
    digest = hashlib.sha256(json.dumps({'order_id':oid,'items':canonical},sort_keys=True).encode()).hexdigest()
    get_db, _, record_movement = dependencies()
    with get_db() as db:
        # Serialize all receipts for this PO, including concurrent independent keys.
        query = 'SELECT id,status FROM purchase_orders WHERE id=? AND business_id=?'
        if __import__('os').environ.get('DATABASE_URL'):
            query += ' FOR UPDATE'
        order = db.execute(query,(oid,bid)).fetchone()
        if order is None:
            return bad('Purchase order not found.',404)
        prior = db.execute('SELECT id,request_hash FROM purchase_order_receipts WHERE business_id=? AND request_key=?',
                           (bid,key)).fetchone()
        if prior:
            if prior['request_hash'] != digest:
                return bad('Receipt key was already used for different quantities.',409)
            return jsonify({'success': True,'replayed':True,'receipt_id':prior['id'],'status':order['status']})
        if order['status'] not in ('ordered','partially_received'):
            return bad('Only ordered purchase orders can receive stock.',409)
        rows = db.execute('''SELECT i.id,i.product_id,i.quantity_ordered,i.quantity_received,p.name
            FROM purchase_order_items i JOIN products p ON p.id=i.product_id AND p.business_id=i.business_id
            WHERE i.purchase_order_id=? AND i.business_id=?''',(oid,bid)).fetchall()
        by_id = {row['id']:row for row in rows}
        for item_id, qty in canonical:
            row = by_id.get(item_id)
            if row is None or row['quantity_received'] + qty > row['quantity_ordered']:
                return bad('Receipt exceeds outstanding quantity or item does not belong to order.',409)
        receipt = db.execute('''INSERT INTO purchase_order_receipts
            (business_id,purchase_order_id,request_key,request_hash) VALUES (?,?,?,?)
            RETURNING id''',(bid,oid,key,digest)).fetchone()
        rid = receipt['id']
        for item_id, qty in canonical:
            row = by_id[item_id]
            updated = db.execute('''UPDATE products SET stock=stock+?
                WHERE id=? AND business_id=? AND stock+?<=100000000''',
                (qty,row['product_id'],bid,qty))
            if updated.rowcount != 1:
                return bad('Stock limit reached; receipt not recorded.', 409)
            after = db.execute('SELECT stock FROM products WHERE id=? AND business_id=?',
                               (row['product_id'],bid)).fetchone()['stock']
            db.execute('''UPDATE purchase_order_items SET quantity_received=quantity_received+?
                WHERE id=? AND business_id=?''',(qty,item_id,bid))
            db.execute('''INSERT INTO purchase_order_receipt_items (receipt_id,po_item_id,quantity)
                VALUES (?,?,?)''',(rid,item_id,qty))
            record_movement(db,bid,row['product_id'],row['name'],after-qty,after,
                            'Purchase order receipt #'+str(oid),'purchase_order',oid)
        remaining = db.execute('''SELECT COUNT(*) FROM purchase_order_items
            WHERE purchase_order_id=? AND business_id=? AND quantity_received<quantity_ordered''',
            (oid,bid)).fetchone()[0]
        status = 'received' if remaining == 0 else 'partially_received'
        db.execute('UPDATE purchase_orders SET status=? WHERE id=? AND business_id=?',(status,oid,bid))
    return jsonify({'success':True,'replayed':False,'receipt_id':rid,'status':status})
