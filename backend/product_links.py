"""Canonical SKU links; never infer the public origin from an untrusted Host header."""
import os
from urllib.parse import quote, urlsplit

from backend.media import public_image_url


def product_path(product_id):
    return f"/products/{quote(str(product_id), safe='')}"


def public_product_url(product_id):
    origin = (os.getenv("PUBLIC_SITE_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()).rstrip("/")
    if not origin:
        return None
    try:
        parsed = urlsplit(origin)
        if parsed.path or parsed.query or parsed.fragment or not public_image_url(origin + "/"):
            return None
    except ValueError:
        return None
    return origin + product_path(product_id)


def public_order_url(product_id):
    url = public_product_url(product_id)
    return url + "#order-form" if url else None
