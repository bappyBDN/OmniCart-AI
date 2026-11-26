"""Product media URLs and a deterministic SVG fallback.

The supplied catalogue contains no product photography. The generated SVG is an
explicit category illustration, not a claim about the product's exact appearance.
An admin can attach a real image URL, which the same endpoint redirects to.
"""
import html
import ipaddress
import os
from urllib.parse import urlsplit, urlunsplit

from backend.database import transaction
from backend.catalog_state import active_product
from backend.records import ProductImage
from models import Product


def _public_origin():
    origin = (os.getenv("PUBLIC_SITE_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip()).rstrip("/")
    if not origin:
        return None
    try:
        parsed = urlsplit(origin)
        if parsed.path or parsed.query or parsed.fragment or parsed.scheme != "https":
            return None
        return origin
    except ValueError:
        return None


def public_image_url(value):
    """Validate a stored URL for Meta to fetch; never fetch user/model URLs here.

    Relative SVG illustration endpoints are for the web, not Messenger photos.
    A syntactically public URL can still fail remotely (auth, expiry, bad MIME).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("/api/catalog-media/"):
        origin = _public_origin()
        if not origin:
            return None
        value = origin + value
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        port = url.port
        if (url.scheme != "https" or not host or url.username or url.password
                or port not in (None, 443) or host == "localhost"
                or host.endswith((".localhost", ".local", ".internal")) or url.path.lower().endswith(".svg")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or not host.isascii():
                return None
        return urlunsplit((url.scheme, url.netloc, url.path, url.query, ""))
    except ValueError:
        return None


def product_image_url(product_id: str) -> str:
    return f"/api/products/{product_id}/image"


def product_image_record(product_id: str, shop_id: str):
    with transaction() as db:
        product = db.query(Product).filter_by(product_id=product_id, shop_id=shop_id).filter(active_product()).first()
        if not product:
            return None, None
        image = db.get(ProductImage, product_id)
        return product, image


def generated_product_svg(product) -> str:
    name = html.escape(product.name or "Product")
    category = html.escape(product.category or "OmniCart")
    palette = [("#dfe9d5", "#b7c9a8"), ("#e8dfcf", "#c9b394"), ("#d8e5e3", "#a9c5bf"),
               ("#e5deeb", "#b9a9c8"), ("#f0e2d9", "#cfb09e")]
    seed = sum(ord(char) for char in (product.product_id or product.name or "P"))
    color_one, color_two = palette[seed % len(palette)]
    initials = html.escape("".join(part[0] for part in (product.name or "P").split()[:3]).upper())
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="720" viewBox="0 0 720 720" role="img" aria-label="{name} illustration">
  <defs>
    <linearGradient id="wash" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#f7faee"/><stop offset="1" stop-color="{color_one}"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="38%" r="60%">
      <stop offset="0" stop-color="#ffffff" stop-opacity=".75"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="720" height="720" fill="url(#wash)"/>
  <circle cx="360" cy="285" r="205" fill="url(#glow)"/>
  <rect x="235" y="250" width="250" height="250" rx="45" fill="{color_two}" opacity=".55"/>
  <text x="360" y="378" text-anchor="middle" font-family="Arial, sans-serif" font-size="78" font-weight="700" fill="#45683a">{initials}</text>
  <text x="360" y="548" text-anchor="middle" font-family="Arial, sans-serif" font-size="23" fill="#5d7c51">{category} · illustration</text>
  <text x="360" y="585" text-anchor="middle" font-family="Arial, sans-serif" font-size="17" fill="#8fa183">{name}</text>
</svg>'''
