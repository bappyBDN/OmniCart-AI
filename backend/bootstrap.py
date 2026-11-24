"""Explicit, non-destructive first-time shop setup: python -m backend.bootstrap."""
import argparse
import getpass
import json

import bcrypt

from backend.config import ROOT, settings
from backend.database import initialize, transaction
from models import Admin, Policy, Product, Shop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true", help="Import supplied JSON into an empty catalog only")
    parser.add_argument("--admin-email", help="Create an admin if the email does not already exist")
    args = parser.parse_args()
    if not args.seed and not args.admin_email:
        parser.error("Specify --seed or --admin-email")
    initialize()
    password_hash = None
    if args.admin_email:
        if "@" not in args.admin_email:
            parser.error("Enter a valid email")
        password = getpass.getpass("New admin password (12+ characters): ")
        if len(password) < 12 or len(password.encode()) > 72:
            parser.error("Password must be at least 12 characters and at most 72 UTF-8 bytes")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords do not match")
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with transaction() as db:
        shop = db.get(Shop, settings.shop_id)
        if not shop:
            shop = Shop(shop_id=settings.shop_id, shop_name="OmniCart")
            db.add(shop)
            db.flush()
        if args.seed:
            if db.query(Product).filter_by(shop_id=settings.shop_id).count() or db.query(Policy).filter_by(shop_id=settings.shop_id).count():
                raise SystemExit("Refusing to seed a populated or partially populated catalog; existing data was not changed.")
            for product in json.loads((ROOT / "Data" / "products.json").read_text(encoding="utf-8")):
                db.add(Product(**product, shop_id=settings.shop_id))
            for policy in json.loads((ROOT / "Data" / "policies.json").read_text(encoding="utf-8")):
                db.add(Policy(policy_type=policy["topic"], policy_text=policy["content"], shop_id=settings.shop_id))
        if args.admin_email:
            if db.query(Admin).filter_by(email=args.admin_email).first():
                raise SystemExit("Admin already exists; password was not changed.")
            db.add(Admin(email=args.admin_email, name="Shop administrator", password_hash=password_hash,
                         role="Manager", shop_id=settings.shop_id))
    print("Setup complete. Existing customers, orders and inventory were not reset.")


if __name__ == "__main__":
    main()
