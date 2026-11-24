# customer_context.py
"""
Fetches the REAL customer profile from the database and formats it
so it can be injected directly into the agent's system prompt.

This is a second, independent safety net on top of `check_user_status_tool`:
even if the LLM forgets to call the tool, or a future edit removes that
instruction from the prompt, the agent still sees ground-truth profile
status on every single turn — so it never has a reason to invent a
customer's name, phone, email, or address.
"""
from core.db_handler import get_db
from models import Customer


def get_customer_profile_status(unique_id: str, shop_id: str = "shop_01") -> str:
    """
    Returns a short, factual summary of what's actually in the database
    for this customer right now.
    """
    db = get_db()
    try:
        customer = db.query(Customer).filter_by(unique_id=unique_id, shop_id=shop_id).first()

        if not customer:
            return (
                "👤 CUSTOMER DB STATUS: No profile exists yet for this user. "
                "ALL fields (name, phone, email, address) must be collected by asking "
                "the user directly — never invent any of them."
            )

        fields = {
            "Name": customer.name,
            "Phone": customer.phone_number,
            "Email": customer.email,
            "Address": customer.address,
        }
        missing = [k for k, v in fields.items() if not v]

        if not missing:
            return (
                f"👤 CUSTOMER DB STATUS: Profile COMPLETE. "
                f"Name={customer.name}, Phone={customer.phone_number}, "
                f"Email={customer.email}, Address={customer.address}. "
                f"You may proceed straight to placing the order."
            )

        present = {k: v for k, v in fields.items() if v}
        return (
            f"👤 CUSTOMER DB STATUS: Profile INCOMPLETE. "
            f"Already saved: {present if present else 'nothing'}. "
            f"STILL MISSING: {', '.join(missing)}. "
            f"Ask the user directly for ONLY these missing fields — do not invent them."
        )
    finally:
        db.close()