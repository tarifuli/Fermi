"""Business settings and data-derived first-time setup checklist."""
import re
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from functools import wraps

onboarding_blueprint=Blueprint('onboarding',__name__)

def deps():
    from app import get_db,require_business
    return get_db,require_business

def login(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        if 'user_id' not in session:return redirect(url_for('login'))
        return fn(*args,**kwargs)
    return wrapped

def onboarding_status(db,bid):
    if bid is None:return {'steps':[],'completed':0,'total':0}
    detail=db.execute('SELECT contact_email,contact_phone,address FROM business_details WHERE business_id=?',(bid,)).fetchone()
    products=db.execute('SELECT COUNT(*) FROM products WHERE business_id=?',(bid,)).fetchone()[0]
    invoices=db.execute('SELECT COUNT(*) FROM invoices WHERE business_id=?',(bid,)).fetchone()[0]
    settings=db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',(bid,)).fetchone()
    steps=[
        {'title':'Complete business contact details','done':bool(detail and detail['contact_email'] and detail['contact_phone'] and detail['address']),'url':'/business/settings'},
        {'title':'Add your first product','done':products>0,'url':'/products'},
        {'title':'Set your inventory alert threshold','done':settings is not None,'url':'/business/settings'},
        {'title':'Create your first invoice','done':invoices>0,'url':'/invoices/new'},
    ]
    return {'steps':steps,'completed':sum(x['done'] for x in steps),'total':len(steps)}

@onboarding_blueprint.route('/business/settings',methods=['GET','POST'])
@login
def business_settings():
    get_db,require_business=deps()
    bid,error=require_business()
    if error:return redirect(url_for('business_setup'))
    if request.method=='POST':
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return jsonify({'success':False,'error':'Invalid settings request.'}),400
        name,email,phone,address=(data.get(k) for k in ('name','contact_email','contact_phone','address'))
        if not all(isinstance(v,str) for v in (name,email,phone,address)):
            return jsonify({'success':False,'error':'All profile fields must be text.'}),400
        name,email,phone,address=(v.strip() for v in (name,email,phone,address))
        if not 1<=len(name)<=120 or len(email)>254 or len(phone)>40 or len(address)>500 or (email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)):
            return jsonify({'success':False,'error':'Check business name, email, phone, and address lengths and format.'}),400
        threshold=data.get('low_stock_threshold')
        if type(threshold) is not int or not 0<=threshold<=100000000:
            return jsonify({'success':False,'error':'Inventory threshold must be a whole number between 0 and 100,000,000.'}),400
        with get_db() as db:
            membership=db.execute("SELECT role FROM business_memberships WHERE business_id=? AND user_id=?",(bid,session['user_id'])).fetchone()
            if not membership or membership['role']!='owner':
                return jsonify({'success':False,'error':'Only business owners can update settings.'}),403
            db.execute('UPDATE businesses SET name=? WHERE id=?',(name,bid))
            db.execute("""INSERT INTO business_details (business_id,contact_email,contact_phone,address)
              VALUES (?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET
              contact_email=excluded.contact_email,contact_phone=excluded.contact_phone,address=excluded.address""",(bid,email,phone,address))
            db.execute("""INSERT INTO inventory_settings (business_id,low_stock_threshold) VALUES (?,?)
              ON CONFLICT(business_id) DO UPDATE SET low_stock_threshold=excluded.low_stock_threshold""",(bid,threshold))
        return jsonify({'success':True})
    with get_db() as db:
        business=db.execute("""SELECT b.name,d.contact_email,d.contact_phone,d.address
          FROM businesses b LEFT JOIN business_details d ON d.business_id=b.id WHERE b.id=?""",(bid,)).fetchone()
        setting=db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',(bid,)).fetchone()
        progress=onboarding_status(db,bid)
    return render_template('business_settings.html',business=dict(business),threshold=setting['low_stock_threshold'] if setting else 20,progress=progress)
