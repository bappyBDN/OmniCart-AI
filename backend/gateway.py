"""Typed, read-only database gateway shared by Graph RAG and the MCP server.

The gateway is deliberately not a SQL console. Every method is scoped to one
shop, validates ownership, and returns JSON-safe records. Writes remain in the
authenticated FastAPI admin/customer services.
"""
from __future__ import annotations

import os
import re

from fastapi import HTTPException
from sqlalchemy import String, case, cast, func, or_

from backend.catalog import catalog_summary
from backend.database import transaction
from backend.catalog_state import active_product
from backend.retrieval import product_dict, tokens
from models import ChatHistory, Customer, Escalation, Order, Policy, Product

DEFAULT_SHOP_ID = os.getenv("MCP_SHOP_ID") or os.getenv("SHOP_ID", "shop_01")
MAX_LIMIT = 50


def _limit(value, default=10):
    try:
        return max(1, min(MAX_LIMIT, int(value)))
    except (TypeError, ValueError):
        return default


def _safe_text(value, length=500):
    return str(value or "")[:length]


def _order(order):
    return {
        "order_id": order.order_id,
        "reference": f"ORD_{order.order_id}",
        "customer_id": order.customer_id,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "unit_price": order.price,
        "total_price": order.total_price,
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


def _policy(policy):
    return {"policy_id": policy.policy_id, "policy_type": policy.policy_type,
            "policy_text": policy.policy_text, "source": "policy"}


class LocalGateway:
    """Same process, same database, no network. Used by tests and by MCP server."""

    def __init__(self, shop_id: str | None = None):
        self.shop_id = shop_id or DEFAULT_SHOP_ID

    def catalog_summary(self, category=None):
        return catalog_summary(self.shop_id, category)

    def search_products(self, query="", category=None, color=None, size=None,
                        max_price=None, min_price=None, in_stock=None, limit=10):
        limit = _limit(limit)
        query = _safe_text(query, 300)
        with transaction() as db:
            products = db.query(Product).filter_by(shop_id=self.shop_id).filter(active_product())
            if category:
                products = products.filter(func.lower(Product.category) == str(category).casefold())
            if color:
                products = products.filter(cast(Product.colors, String).ilike(f"%{_safe_text(color, 40)}%"))
            if size:
                products = products.filter(cast(Product.sizes, String).ilike(f'%"{_safe_text(size, 20)}"%'))
            effective = case((Product.discounted_price.is_not(None) & (Product.discounted_price < Product.price),
                              Product.discounted_price), else_=Product.price)
            if max_price is not None:
                products = products.filter(effective <= float(max_price))
            if min_price is not None:
                products = products.filter(effective >= float(min_price))
            if in_stock is True:
                products = products.filter(Product.stock_count > 0)
            elif in_stock is False:
                products = products.filter(Product.stock_count <= 0)
            terms = tokens(query)[:12]
            if terms:
                conditions = []
                for term in terms:
                    pattern = f"%{term}%"
                    conditions.append(or_(
                        Product.product_id.ilike(pattern), Product.name.ilike(pattern),
                        Product.category.ilike(pattern), Product.description.ilike(pattern),
                        cast(Product.colors, String).ilike(pattern), cast(Product.sizes, String).ilike(pattern),
                    ))
                products = products.filter(or_(*conditions))
            rows = products.order_by(Product.stock_count.desc(), Product.name).limit(limit * 2).all()
        ranked = []
        folded = {term for term in terms if term}
        for product in rows:
            data = product_dict(product)
            title = data["name"].casefold()
            score = sum(1 for term in folded if term in title) * 3
            score += sum(1 for term in folded if term in (data["description"] or "").casefold())
            ranked.append((score, data))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [data for _, data in ranked[:limit]]

    def get_product(self, product_id):
        with transaction() as db:
            product = db.query(Product).filter_by(product_id=str(product_id), shop_id=self.shop_id).filter(active_product()).first()
            if not product:
                raise HTTPException(404, "Product not found in this shop")
            return product_dict(product)

    def product_aliases(self, product_id):
        from backend.records import ProductAlias
        with transaction() as db:
            return [row.alias for row in db.query(ProductAlias).filter_by(shop_id=self.shop_id, product_id=str(product_id))]

    def search_policies(self, query="", limit=10):
        limit = _limit(limit)
        terms = tokens(_safe_text(query, 300))[:10]
        with transaction() as db:
            policies = db.query(Policy).filter_by(shop_id=self.shop_id).all()
        ranked = []
        for policy in policies:
            title = policy.policy_type.casefold()
            body = policy.policy_text.casefold()
            score = sum(3 for term in terms if term in title)
            score += sum(1 for term in terms if term in body)
            if score or not terms:
                ranked.append((score, policy))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [_policy(policy) for _, policy in ranked[:limit]]

    def get_policy(self, policy_id):
        with transaction() as db:
            policy = db.query(Policy).filter_by(policy_id=int(policy_id), shop_id=self.shop_id).first()
            if not policy:
                raise HTTPException(404, "Policy not found in this shop")
            return _policy(policy)

    def list_knowledge(self, limit=20):
        from backend.records import KnowledgeDocument, KnowledgeVersion
        with transaction() as db:
            rows = db.query(KnowledgeDocument, KnowledgeVersion).join(
                KnowledgeVersion,
                (KnowledgeVersion.document_id == KnowledgeDocument.document_id) &
                (KnowledgeVersion.version == KnowledgeDocument.published_version),
            ).filter(KnowledgeDocument.shop_id == self.shop_id).limit(_limit(limit)).all()
        return [{"document_id": document.document_id, "title": document.title, "category": document.category,
                 "locale": document.locale, "version": version.version, "content": version.content} for document, version in rows]

    def customer_profile(self, customer_id):
        with transaction() as db:
            customer = db.query(Customer).filter_by(customer_id=int(customer_id), shop_id=self.shop_id).first()
            if not customer:
                raise HTTPException(404, "Customer not found in this shop")
            return {"customer_id": customer.customer_id, "name": customer.name, "phone": customer.phone_number,
                    "email": customer.email, "address": customer.address,
                    "human_intervention": bool(customer.human_intervention)}

    def customer_orders(self, customer_id, limit=20):
        with transaction() as db:
            customer = db.query(Customer).filter_by(customer_id=int(customer_id), shop_id=self.shop_id).first()
            if not customer:
                raise HTTPException(404, "Customer not found in this shop")
            orders = db.query(Order).filter_by(customer_id=customer.customer_id, shop_id=self.shop_id).order_by(Order.order_id.desc()).limit(_limit(limit)).all()
            return [_order(order) for order in orders]

    def get_order(self, order_id=None, reference=None):
        with transaction() as db:
            query = db.query(Order).filter_by(shop_id=self.shop_id)
            if order_id is not None:
                query = query.filter_by(order_id=int(order_id))
            elif reference:
                match = re.search(r"(\d+)", str(reference))
                if not match:
                    raise HTTPException(422, "Invalid order reference")
                query = query.filter_by(order_id=int(match[1]))
            else:
                raise HTTPException(422, "order_id or reference is required")
            order = query.first()
            if not order:
                raise HTTPException(404, "Order not found in this shop")
            return _order(order)

    def recent_orders(self, limit=20):
        with transaction() as db:
            return [_order(order) for order in db.query(Order).filter_by(shop_id=self.shop_id).order_by(Order.order_id.desc()).limit(_limit(limit)).all()]

    def support_cases(self, status=None, limit=20):
        from backend.support import case_dict
        from backend.records import SupportCase
        with transaction() as db:
            query = db.query(Escalation).filter_by(shop_id=self.shop_id)
            if status:
                query = query.filter_by(status=str(status))
            details = {row.case_id: row for row in db.query(SupportCase).filter_by(shop_id=self.shop_id)}
            return [case_dict(case, details.get(case.case_id)) for case in query.order_by(Escalation.case_id.desc()).limit(_limit(limit)).all()]

    def recent_messages(self, customer_id, limit=20):
        with transaction() as db:
            customer = db.query(Customer).filter_by(customer_id=int(customer_id), shop_id=self.shop_id).first()
            if not customer:
                raise HTTPException(404, "Customer not found in this shop")
            rows = db.query(ChatHistory).filter_by(customer_id=customer.customer_id, shop_id=self.shop_id).order_by(ChatHistory.message_id.desc()).limit(_limit(limit)).all()
            return [{"sender": row.sender, "content": row.message_text, "timestamp": row.timestamp.isoformat() if row.timestamp else None} for row in reversed(rows)]

    def metrics_summary(self):
        with transaction() as db:
            orders = db.query(Order).filter_by(shop_id=self.shop_id).all()
            revenue = sum(float(order.total_price or 0) for order in orders if order.status != "Cancelled")
            return {"orders": len(orders), "revenue": round(revenue, 2),
                    "customers": db.query(Customer).filter_by(shop_id=self.shop_id).count(),
                    "open_cases": db.query(Escalation).filter_by(shop_id=self.shop_id).filter(Escalation.status.in_(["Open", "Pending"])).count(),
                    "products": db.query(Product).filter_by(shop_id=self.shop_id).filter(active_product()).count()}

    def database_search(self, query="", product_id=None, category=None, color=None, size=None,
                        currency=None, max_price=None, min_price=None, in_stock=None, quantity=1, limit=20):
        """Join-based, tenant-scoped search with exact stock totals and per-currency inventory value."""
        from backend.database_search import search_database
        return search_database(self.shop_id, query=query, product_id=product_id, category=category, color=color,
                               size=size, currency=currency, max_price=max_price, min_price=min_price,
                               in_stock=in_stock, quantity=quantity, limit=limit)


# JSON-Schema contracts exposed to MCP clients. Descriptions are written for an
# agent, not for humans reading an OpenAPI page.
TOOL_SCHEMAS = [
    {"name": "catalog_summary", "description": "Count the shop's whole catalogue, in-stock/out-of-stock product types and available units. Never use search top-k as a catalogue total.",
     "inputSchema": {"type": "object", "properties": {"category": {"type": "string"}}, "additionalProperties": False}},
    {"name": "catalog_search", "description": "Search live products using SQL filters (category, colour, size, price, stock) and textual matching. Returns authoritative price and stock.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "category": {"type": "string"}, "color": {"type": "string"},
         "size": {"type": "string"}, "max_price": {"type": "number"}, "min_price": {"type": "number"},
         "in_stock": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         "additionalProperties": False}},
    {"name": "catalog_get", "description": "Get one live product by exact product ID.",
     "inputSchema": {"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"], "additionalProperties": False}},
    {"name": "catalog_aliases", "description": "List search aliases for an exact product ID.",
     "inputSchema": {"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"], "additionalProperties": False}},
    {"name": "policies_search", "description": "Search the store's policy documents using live SQL text matching.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}},
    {"name": "policy_get", "description": "Get one policy by exact policy ID.",
     "inputSchema": {"type": "object", "properties": {"policy_id": {"type": "integer"}}, "required": ["policy_id"], "additionalProperties": False}},
    {"name": "knowledge_list", "description": "List published knowledge documents and their currently published version.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}},
    {"name": "customer_get", "description": "Read one customer profile, scoped to this shop.",
     "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"], "additionalProperties": False}},
    {"name": "customer_orders", "description": "Read one customer's real orders, newest first.",
     "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["customer_id"], "additionalProperties": False}},
    {"name": "customer_messages", "description": "Read recent chat messages for one customer in this shop.",
     "inputSchema": {"type": "object", "properties": {"customer_id": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["customer_id"], "additionalProperties": False}},
    {"name": "orders_recent", "description": "Read recent orders for this shop, newest first.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}},
    {"name": "order_get", "description": "Read one order by numeric ID or reference such as ORD_42.",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "reference": {"type": "string"}}, "additionalProperties": False}},
    {"name": "support_cases", "description": "Read the shop's support cases and their current status.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}},
    {"name": "metrics_summary", "description": "Read aggregate commerce and support metrics for this shop.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "database_search", "description": "Search the live shop database with tenant-scoped, parameterized joins and exact arithmetic. Returns matched products plus total stock and per-currency inventory value. Unknown price/stock is reported as null, never zero.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "product_id": {"type": "string"}, "category": {"type": "string"},
         "color": {"type": "string"}, "size": {"type": "string"}, "currency": {"type": "string"},
         "max_price": {"type": "number"}, "min_price": {"type": "number"}, "in_stock": {"type": "boolean"},
         "quantity": {"type": "integer", "minimum": 1, "maximum": 1000},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}},
]

# Explicit mapping is intentionally boring: no dynamic SQL, no arbitrary tool names.
DISPATCH = {
    "catalog_summary": "catalog_summary",
    "catalog_search": "search_products",
    "catalog_get": "get_product",
    "catalog_aliases": "product_aliases",
    "policies_search": "search_policies",
    "policy_get": "get_policy",
    "knowledge_list": "list_knowledge",
    "customer_get": "customer_profile",
    "customer_orders": "customer_orders",
    "customer_messages": "recent_messages",
    "orders_recent": "recent_orders",
    "order_get": "get_order",
    "support_cases": "support_cases",
    "metrics_summary": "metrics_summary",
    "database_search": "database_search",
}
ALLOWED_ARGS = {
    "catalog_summary": {"category"},
    "catalog_search": {"query", "category", "color", "size", "max_price", "min_price", "in_stock", "limit"},
    "catalog_get": {"product_id"},
    "catalog_aliases": {"product_id"},
    "policies_search": {"query", "limit"},
    "policy_get": {"policy_id"},
    "knowledge_list": {"limit"},
    "customer_get": {"customer_id"},
    "customer_orders": {"customer_id", "limit"},
    "customer_messages": {"customer_id", "limit"},
    "orders_recent": {"limit"},
    "order_get": {"order_id", "reference"},
    "support_cases": {"status", "limit"},
    "metrics_summary": set(),
    "database_search": {"query", "product_id", "category", "color", "size", "currency", "max_price", "min_price", "in_stock", "quantity", "limit"},
}


def call_tool(name: str, arguments: dict, gateway: LocalGateway | None = None):
    if name not in DISPATCH:
        raise ValueError(f"Unknown MCP tool: {name}")
    arguments = arguments or {}
    unknown = set(arguments) - ALLOWED_ARGS[name]
    if unknown:
        raise ValueError(f"Unsupported arguments for {name}: {sorted(unknown)}")
    gateway = gateway or LocalGateway()
    method = getattr(gateway, DISPATCH[name])
    return method(**arguments)
