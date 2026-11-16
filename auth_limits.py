"""Database-backed login attempt throttling, shared across serverless instances."""
import hashlib
from datetime import datetime, timedelta, timezone

WINDOW_SECONDS=900
MAX_ATTEMPTS=8

def _key(email, ip):
    return hashlib.sha256((email.lower().strip()+'|'+ip).encode()).hexdigest()

def is_limited(db, email, ip):
    key=_key(email,ip)
    row=db.execute('SELECT attempts, window_start FROM auth_login_limits WHERE key=?',(key,)).fetchone()
    if not row:return False
    start=row['window_start']
    if isinstance(start,str):start=datetime.fromisoformat(start.replace('Z','+00:00'))
    if start.tzinfo is None:start=start.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)-start<timedelta(seconds=WINDOW_SECONDS) and row['attempts']>=MAX_ATTEMPTS

def record_failure(db,email,ip):
    key=_key(email,ip)
    now=datetime.now(timezone.utc)
    # Atomic increment; no in-process state, so multiple serverless instances share limits.
    db.execute("""INSERT INTO auth_login_limits(key,attempts,window_start)
        VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET
        attempts=CASE WHEN auth_login_limits.window_start < ? THEN 1 ELSE auth_login_limits.attempts+1 END,
        window_start=CASE WHEN auth_login_limits.window_start < ? THEN excluded.window_start ELSE auth_login_limits.window_start END""",
        (key,now.isoformat(),(now-timedelta(seconds=WINDOW_SECONDS)).isoformat(),(now-timedelta(seconds=WINDOW_SECONDS)).isoformat()))

def clear_failures(db,email,ip):
    db.execute('DELETE FROM auth_login_limits WHERE key=?',(_key(email,ip),))
