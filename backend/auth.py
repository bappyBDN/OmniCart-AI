import hashlib
import secrets
from datetime import datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.database import transaction
from backend.records import AccessSession

bearer = HTTPBearer(auto_error=False)


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db, role, shop_id, customer_id=None, admin_id=None):
    token = secrets.token_urlsafe(40)
    db.add(AccessSession(token_hash=token_digest(token), role=role, shop_id=shop_id,
                         customer_id=customer_id, admin_id=admin_id,
                         expires_at=datetime.utcnow() + timedelta(days=7 if role == "customer" else 1)))
    return token


def current_session(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, "Session required")
    with transaction() as db:
        session = db.get(AccessSession, token_digest(credentials.credentials))
        if not session or session.expires_at < datetime.utcnow():
            raise HTTPException(401, "Session expired. Please sign in again.")
        return session


def customer_session(session=Depends(current_session)):
    if session.role != "customer":
        raise HTTPException(403, "Customer session required")
    return session


def admin_session(request: Request, session=Depends(current_session)):
    if session.role != "admin":
        raise HTTPException(403, "Admin session required")
    with transaction() as db:
        from models import Admin
        admin = db.query(Admin).filter_by(admin_id=session.admin_id, shop_id=session.shop_id).first()
        role = (admin.role or "").lower().replace("_", " ") if admin else ""
    path = request.url.path
    # Fail closed for unknown roles. Preserve legacy Owner/Manager accounts.
    full = role in {"owner", "manager", "admin", "super admin"}
    read_only = role in {"analyst", "auditor", "read only"} and request.method == "GET"
    support = role in {"support agent", "agent"} and any(word in path for word in ("/cases", "/customers", "/dashboard", "/team"))
    catalogue = role in {"catalogue manager", "catalogue"} and any(word in path for word in ("/products", "/knowledge", "/policies", "/dashboard"))
    if not (full or read_only or support or catalogue):
        raise HTTPException(403, "Your admin role does not allow this operation")
    return session


def verify_password(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False
