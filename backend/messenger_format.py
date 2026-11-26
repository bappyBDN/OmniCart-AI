"""Plain-text Messenger rendering and fresh, shop-scoped product photography.

Never mutate the canonical ChatTurn response: the web still needs its citations
and structured sources. Only trusted SQL image records are sent to Meta.
"""
import re
from dataclasses import dataclass, field

from backend.database import transaction
from backend.language import is_bangla
from backend.media import public_image_url
from backend.records import ProductImage
from backend.retrieval import product_dict
from models import Product

PRODUCT_TAG = re.compile(r"\[\s*(?:source_id:\s*)?product:(SKU_[A-Z0-9_-]+)\s*\]", re.I)
SOURCE_TAG = re.compile(r"\[\s*(?:source_id:\s*)?(?:product|policy|knowledge|catalog):[^\]\n]+\]", re.I)


@dataclass
class MessengerReply:
    text: str
    products: list[dict]           # products with a real public photo (native image mode)
    cards: list[dict] = field(default_factory=list)  # every cited product, for Generic Template cards


def parse_messenger_text(answer):
    """Extract ordered, unique product IDs and remove channel-inappropriate markup."""
    text = re.sub(r"【([^】]+)】", r"[\1]", answer or "")
    product_ids = list(dict.fromkeys(match.upper() for match in PRODUCT_TAG.findall(text)))
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # Do not send model-authored image URLs.
    text = re.sub(r"\[\s*((?:product|policy|knowledge|catalog):[^\]]+)\]\([^)]*\)", r"[\1]", text, flags=re.I)
    text = re.sub(r"\[(?!\s*(?:product|policy|knowledge|catalog):)([^\]]+)\]\([^)]*\)", r"\1", text, flags=re.I)
    text = SOURCE_TAG.sub("", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"(?m)^\s*\*\s+", "• ", text)
    text = text.replace("**", "").replace("__", "").replace("~~", "")
    text = re.sub(r"(?m)^```[^\n]*\n?|```", "", text)
    text = text.replace("`", "")
    text = re.sub(r"(?<!\w)[*_]([^\n*_]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, product_ids


def format_messenger_response(response, shop_id, query=""):
    """Hydrate cited SKUs, never use cached stock or image URLs from the model."""
    text, product_ids = parse_messenger_text(response.get("answer", ""))
    sources = [s for s in response.get("sources", []) if s.get("kind") == "product" and s.get("data")]
    allowed = {s["data"]["product_id"] for s in sources}
    # Source-backed replies may not display uncited/invented retrieval candidates.
    if sources:
        product_ids = [product_id for product_id in product_ids if product_id in allowed]
    explicit_photo = bool(re.search(r"\b(?:images?|photos?|pictures?)\b|ছবি", query, re.I))
    show_images = response.get("show_images", True) or explicit_photo
    if not show_images and text:
        return MessengerReply(text, [])
    if not product_ids and show_images:
        focus = response.get("focus_product_id")
        if focus in allowed:
            product_ids = [focus]
    product_ids = product_ids[:5]
    if not product_ids:
        return MessengerReply(text, [])
    with transaction() as db:
        rows = db.query(Product).filter(Product.shop_id == shop_id, Product.product_id.in_(product_ids)).all()
        images = db.query(ProductImage).filter(ProductImage.shop_id == shop_id,
                                               ProductImage.product_id.in_(product_ids)).all()
        by_id = {row.product_id: product_dict(row) for row in rows}
        by_image = {image.product_id: image for image in images}
        products, missing, representative, cards = [], [], [], []
        for product_id in product_ids:
            data = by_id.get(product_id)
            if not data:
                continue
            image = by_image.get(product_id)
            url = public_image_url(image.image_url) if image else None
            if show_images:
                cards.append({**data, "image_url": url})
            if show_images and url:
                products.append({**data, "image_url": url, "alt_text": image.alt_text})
                if re.search(r"representative|illustration", image.alt_text or "", re.I):
                    representative.append(data["name"])
            elif explicit_photo:
                missing.append(data["name"])
        if not text and by_id:
            text = "\n".join(f"{by_id[pid]['name']} — ${by_id[pid]['effective_price']:.2f} USD; "
                             f"{by_id[pid]['stock_count']} in stock." for pid in product_ids if pid in by_id)
    if missing:
        note = (f"এখন {', '.join(missing)}-এর কোনো পণ্যের ছবি দেওয়া নেই।" if is_bangla(query) else
                f"I don't have a product photo for {', '.join(missing)} yet.")
        text = (text + "\n" + note).strip()
    if representative:
        note = (f"{', '.join(representative)}-এর ছবিটি প্রতিনিধিত্বমূলক; হুবহু এই SKU-এর ছবি নাও হতে পারে।" if is_bangla(query) else
                f"The photo for {', '.join(representative)} is representative and may not show the exact SKU.")
        text = (text + "\n" + note).strip()
    return MessengerReply(text, products, cards)
