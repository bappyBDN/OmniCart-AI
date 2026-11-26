"""Production-shaped hybrid RAG for the web assistant.

Pipeline
--------
Live SQL/published knowledge
  -> deterministic Bangla/Banglish query plan
  -> SQL-first product filters (colour, size, budget, stock, category)
  -> structure-aware policy chunks
  -> BM25 lexical ranking + optional multilingual dense ranking
  -> reciprocal-rank fusion
  -> explainable reranking and a strict relevance gate
  -> cited source records for the agent/UI

The dense model is optional and warms in a background thread. If it is absent,
lexical retrieval still works. SQL remains the sole source of price and stock.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from langchain_core.documents import Document
from sqlalchemy import String, case, cast, func, or_

from backend.config import settings
from backend.catalog_state import active_product, orderable
from backend.database import transaction
from backend.language import normalize_query
from backend.media import product_image_url
from backend.records import KnowledgeDocument, KnowledgeVersion, ProductAlias
from models import Policy, Product

log = logging.getLogger(__name__)

# Generic question/function words do not carry retrieval evidence. Bangla forms
# are included so short Bangla questions still work without a tokenizer model.
STOP = set("""
a an the is are was were be been being am do does did i me my mine we our ours us
you your yours he she it its they them their this that these those for of to in on
at by with and or but if then than as from about into over under again further once
here there when where why how all any both each few more most other some such no nor
not only own same so too very can could should would will just now please show find
out sold
have has had having tell want wants buy order orders product products policy policies
available price stock what which who whom whose whats hows tell me us our different
difference between something anything everything nothing thing things tell know need
needs give get got make made let lets see look looking help thanks thank hello hi
show me items item catalogue catalog inventory units unit pieces piece count total
currently store shop your yours apnar apnader kache mot koyta koyti kotogulo koto koy
ache ase apni tomader ponno ponnyo dam daam
আমি আমার আমাদের আপনার আপনাদের কি কী কত কতটা কতটি কতগুলো কতগুলি আছে আছেন হয় হবে করে করব
চাই চান দেখান দেখতে বলুন বলবে জন্য থেকে সাথে এবং বা কিন্তু না এর ও এই সেই একটি একটা টি টা
দাম পণ্য প্রোডাক্ট পলিসি নিয়ম নীতি বর্তমানে এখন মোট সংখ্যা প্রকার ধরনের জানতে চাই
""".split())

# Canonical product/policy vocabulary. Keep these deterministic: no model call is
# needed just to understand a colour, size, budget or Banglish spelling.
ALIASES = {
    "টিশার্ট": "t shirt", "টি-শার্ট": "t shirt", "টীশার্ট": "t shirt", "শার্ট": "shirt", "জামা": "shirt",
    "প্যান্ট": "pant", "জুতা": "shoe", "জুতো": "shoe", "ঘড়ি": "watch", "ঘড়ি": "watch",
    "হেডফোন": "headphone", "ইয়ারবাড": "earbud", "ইয়ারবাড": "earbud", "ব্যাগ": "backpack",
    "ওয়ালেট": "wallet", "মানিব্যাগ": "wallet", "কিবোর্ড": "keyboard", "মাউস": "mouse",
    "হুডি": "hoodie", "বোতল": "bottle", "বাতি": "lamp", "যোগা": "yoga", "ম্যাট": "mat",
    "রিফান্ড": "refund", "টাকা ফেরত": "refund", "ফেরত": "return", "রিটার্ন": "return",
    "ডেলিভারি": "delivery shipping", "শিপিং": "shipping", "পেমেন্ট": "payment",
    "চার্জ": "charge", "খরচ": "charge cost", "শহর": "city", "শহরের": "city",
    "ভিতরে": "inside", "ভেতরে": "inside", "বাইরে": "outside",
    "ওয়ারেন্টি": "warranty", "ওয়ারেন্টি": "warranty", "বাতিল": "cancellation",
    "বিনিময়": "exchange", "এক্সচেঞ্জ": "exchange", "নষ্ট": "damaged", "ভাঙা": "damaged",
    "ছাড়": "discount", "ছাড়": "discount", "রঙ": "color", "কালো": "black", "নীল": "blue",
    "লাল": "red", "সাদা": "white", "সবুজ": "green", "শহরে": "inside city", "বাইরে": "outside city",
    "গিফট": "gift", "উপহার": "gift", "মোড়ক": "wrapping", "মোড়ক": "wrapping",
    # English morphology that matters for policy topics.
    "cancel": "cancel cancellation", "cancellation": "cancellation cancel",
    "deliver": "deliver delivery", "delivery": "delivery",
    "pay": "pay payment", "payment": "payment",
    "ship": "ship shipping", "shipping": "shipping",
    "damage": "damage damaged", "damaged": "damaged",
    "exchange": "exchange", "return": "return",
    "subscribe": "subscribe subscription", "subscription": "subscription",
    "crypto": "crypto cryptocurrency", "cryptocurrency": "cryptocurrency",
    "authentic": "authentic authenticity", "authenticity": "authenticity",
    "retain": "retain retention", "retention": "retention",
    "modify": "modify modification", "modification": "modification modifications",
    "promo": "promo promotional", "promotional": "promotional",
    "installment": "installment installments", "installments": "installments",
    "discount": "discount discounts", "discounts": "discounts",
    "warranty": "warranty", "refund": "refund refunds", "refunds": "refunds",
    "gray": "grey", "grey": "grey",
}

COLOR_CANONICAL = {
    "black", "blue", "red", "white", "green", "brown", "grey", "purple", "silver",
    "gold", "navy", "beige", "clear", "olive", "khaki", "orange", "pink", "yellow",
    "wine", "midnight", "stainless", "terracotta", "floral", "assorted", "cream",
    "burgundy", "lavender", "multicolor",
}

SIZE_WORDS = {
    "small": "S", "medium": "M", "large": "L", "extra large": "XL", "extra-large": "XL",
    "ছোট": "S", "মাঝারি": "M", "বড়": "L", "বড়": "L",
}

CATEGORY_WORDS = {
    "electronics": "Electronics", "electronic": "Electronics",
    "apparel": "Apparel", "clothing": "Apparel", "clothes": "Apparel",
    "accessories": "Accessories", "accessory": "Accessories",
    "home": "Home", "sports": "Sports", "sport": "Sports",
    "stationery": "Stationery", "health": "Health", "beauty": "Beauty",
    "toys": "Toys", "toy": "Toys", "tools": "Tools", "tool": "Tools", "grocery": "Grocery",
}

TYPE_GROUPS = (
    {"shirt", "tshirt"}, {"pant", "sweatpant"}, {"short"}, {"shoe", "sneaker", "sandal"},
    {"watch"}, {"wallet"}, {"earbud"}, {"headphone"}, {"keyboard"}, {"mouse"},
    {"backpack"}, {"hoodie"}, {"bottle"}, {"lamp"}, {"mat"}, {"mug"}, {"dress"},
    {"jacket"}, {"blender"}, {"monitor"}, {"speaker"}, {"phone"}, {"laptop"},
    {"saree", "jamdani"},
)
ACCESSORY_WORDS = {"case", "holder", "band", "sleeve", "stand", "rest", "cable"}

POLICY_DEFAULTS = {
    "refund": "Refund Policy", "return": "Return Window",
    "shipping": "Shipping Charges & Time", "delivery": "Shipping Charges & Time",
    "payment": "Payment Methods", "warranty": "Warranty Coverage",
    "cancellation": "Order Cancellation", "exchange": "Exchange Policy",
}


def _normalise_spaces(text: str) -> str:
    return " ".join(text.split())


def expand(text: str) -> str:
    """Normalise Bangla/Banglish and expand only whole words, never substrings."""
    value = normalize_query(text or "").text
    value = value.replace("t-shirt", "tshirt").replace("t shirt", "tshirt")
    # Longest first prevents "শহর" from partially consuming "শহরের".
    for source in sorted(ALIASES, key=len, reverse=True):
        if source not in value:
            continue
        value = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", f" {ALIASES[source]} ", value)
    return _normalise_spaces(value.replace("t shirt", "tshirt"))


def _stem(word: str) -> str:
    if not word.isascii():
        return word
    if word.endswith("ies") and len(word) > 4:
        return f"{word[:-3]}y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _token_list(normalised: str) -> list[str]:
    # Documents are stored with their display casing; matching must be case-insensitive.
    words = re.findall(r"[\w\u0980-\u09ff]+", (normalised or "").casefold(), re.UNICODE)
    return [stemmed for word in words if len(word) > 1 and not word.isdigit()
            and (stemmed := _stem(word)) not in STOP]


def tokens(text: str) -> list[str]:
    return _token_list(expand(text))


def budget_limit(query: str):
    patterns = (
        r"(?:under|below|less than|within|max(?:imum)?|budget(?: of)?|up to|মধ্যে|নিচে|কম)\s*[$৳]?\s*([\d,]+(?:\.\d+)?)",
        r"([\d,]+(?:\.\d+)?)\s*(?:under|within|dollars?|taka|tk|৳|bdt)",
    )
    for pattern in patterns:
        match = re.search(pattern, query, re.I)
        if match:
            return float(match[1].replace(",", ""))
    return None


def minimum_price(query: str):
    match = re.search(r"(?:over|above|more than|starting at)\s*[$৳]?\s*([\d,]+(?:\.\d+)?)", query, re.I)
    return float(match[1].replace(",", "")) if match else None


@dataclass(frozen=True)
class QueryPlan:
    original: str
    normalised: str
    locale: str
    terms: tuple[str, ...]
    colors: frozenset[str]
    size: str | None
    max_price: float | None
    min_price: float | None
    in_stock: bool | None
    category: str | None
    groups: tuple[frozenset[str], ...]


def _extract_size(query: str) -> str | None:
    match = re.search(r"(?i)\bsize\s*[:=]?\s*(xxl|xl|xs|s|m|l|\d{1,2})\b", query)
    if match:
        return match.group(1).upper()
    match = re.search(r"\b(XXL|XL|XS|S|M|L)\b", query)
    if match:
        return match.group(1)
    normalised = normalize_query(query).text
    for word, size in SIZE_WORDS.items():
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", normalised, re.I):
            return size
    return None


def analyse_query(query: str) -> QueryPlan:
    normalised = expand(query)
    term_list = _token_list(normalised)
    term_set = set(term_list)
    colors = frozenset(term_set & COLOR_CANONICAL)
    size = _extract_size(query)
    stock_state = None
    if re.search(r"out of stock|sold out|স্টক\s*নেই|স্টকে\s*নেই|stock\s*শূন্য", normalised, re.I):
        stock_state = False
    elif re.search(r"in stock|available|স্টকে|আছে|পাওয়া\s*যায়|পাওয়া\s*যায়", normalised, re.I):
        stock_state = True
    category = None
    for word, category_name in CATEGORY_WORDS.items():
        if word in term_set:
            category = category_name
            break
    # "home delivery" is a policy phrase, not a request for the Home category.
    if category == "Home" and ({"delivery", "shipping"} & term_set):
        category = None
    groups = tuple(group for group in TYPE_GROUPS if group & term_set)
    return QueryPlan(
        original=query,
        normalised=normalised,
        locale=normalize_query(query).locale,
        terms=tuple(sorted(term_set)),
        colors=colors,
        size=size,
        max_price=budget_limit(normalised),
        min_price=minimum_price(normalised),
        in_stock=stock_state,
        category=category,
        groups=groups,
    )


def product_dict(product) -> dict:
    discount = product.discounted_price
    effective = discount if discount is not None and product.price is not None and 0 <= discount < product.price else product.price
    entry = product.catalog_entry
    payload = entry.payload if entry else {}
    data = {
        "product_id": product.product_id, "name": product.name, "category": product.category,
        "price": product.price, "discounted_price": discount, "effective_price": effective,
        "stock_count": product.stock_count, "status": "Needs confirmation" if product.stock_count is None else "Out of Stock" if product.stock_count <= 0 else "In Stock",
        "colors": product.colors or [], "sizes": product.sizes or [], "description": product.description or "",
        "image_url": product_image_url(product.product_id),
        "currency": entry.currency if entry else "BDT", "active": entry.active if entry else True,
        "localized": payload.get("name", {}), "review": payload.get("review", {}),
        "observed_colors": payload.get("visual_attributes", {}).get("observed_colors", []),
        "gallery": ["/api/catalog-media/" + image["path"].rsplit("/", 1)[-1] for image in payload.get("images", [])],
        "publication_status": "draft_requires_merchant_verification" if entry else "legacy",
    }
    data["orderable"] = orderable(data)
    return data


def split_policy(text: str, size: int = 650, overlap: int = 90) -> list[str]:
    """Paragraph/sentence-aware chunks with bounded overlap.

    The exact same chunker is used for source policies and published knowledge,
    so a version change cannot alter boundaries silently in a different module.
    """
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            floor = start + max(1, size // 2)
            candidates = [
                text.rfind("\n\n", floor, end), text.rfind("\n", floor, end),
                text.rfind(". ", floor, end), text.rfind("! ", floor, end),
                text.rfind("? ", floor, end), text.rfind("।", floor, end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


class HybridRetriever:
    def __init__(self, dense_enabled: bool | None = None):
        self.dense_enabled = settings.dense_enabled if dense_enabled is None else dense_enabled
        self.min_confidence = settings.rag_min_confidence
        self.dense_min_similarity = settings.rag_dense_min_similarity
        self.lock = threading.Lock()
        self.indexes: dict[str, tuple[str, dict[str, list[float]]]] = {}
        self.loading = False
        self.failed = False
        self.embedding = None
        self.dense_status = "not_started" if self.dense_enabled else "disabled"

    # ------------------------------------------------------------------
    # Corpus construction
    # ------------------------------------------------------------------
    def _product_documents(self, db, shop_id: str, plan: QueryPlan | None) -> list[Document]:
        query = db.query(Product).filter_by(shop_id=shop_id).filter(active_product())
        if plan:
            if plan.category:
                query = query.filter(func.lower(Product.category) == plan.category.lower())
            if plan.colors:
                query = query.filter(or_(*[cast(Product.colors, String).ilike(f"%{color}%") for color in plan.colors]))
            if plan.size:
                query = query.filter(cast(Product.sizes, String).ilike(f'%"{plan.size}"%'))
            effective = case((Product.discounted_price.is_not(None) & (Product.discounted_price < Product.price),
                              Product.discounted_price), else_=Product.price)
            if plan.max_price is not None:
                query = query.filter(effective <= plan.max_price)
            if plan.min_price is not None:
                query = query.filter(effective >= plan.min_price)
            if plan.in_stock is True:
                query = query.filter(Product.stock_count > 0)
            elif plan.in_stock is False:
                query = query.filter(Product.stock_count <= 0)
        aliases: dict[str, list[str]] = {}
        for alias in db.query(ProductAlias).filter_by(shop_id=shop_id):
            aliases.setdefault(alias.product_id, []).append(alias.alias)
        documents = []
        for product in query.all():
            data = product_dict(product)
            alias_text = " ".join(aliases.get(product.product_id, []))
            embedding_text = " ".join(filter(None, [
                product.product_id, product.name, product.category,
                " ".join(product.colors or []), " ".join(product.sizes or []),
                alias_text, product.description or "",
                " ".join(doc["text"] for doc in product.catalog_entry.documents) if product.catalog_entry else "",
            ]))
            documents.append(Document(
                page_content=json.dumps(data, ensure_ascii=False),
                metadata={
                    "id": f"product:{product.product_id}", "kind": "product", "title": product.name,
                    "shop_id": shop_id, "data": data, "embedding_text": embedding_text,
                    "keywords": f"{product.product_id} {product.name} {product.name} {product.category} "
                                f"{' '.join(product.colors or [])} {' '.join(product.sizes or [])} {alias_text} "
                                f"{product.description or ''}",
                },
            ))
        return documents

    def _policy_documents(self, db, shop_id: str) -> list[Document]:
        documents = []
        from backend.records import PolicySource
        localized = {row.policy_id: row for row in db.query(PolicySource).filter_by(shop_id=shop_id)}
        for policy in db.query(Policy).filter_by(shop_id=shop_id).all():
            source = localized.get(policy.policy_id)
            text = policy.policy_text
            if source:
                text += "\n" + "\n".join(source.translations.values())
            for index, chunk in enumerate(split_policy(text)):
                embedding_text = f"{policy.policy_type} {policy.policy_type} {chunk}"
                documents.append(Document(
                    page_content=chunk,
                    metadata={
                        "id": f"policy:{policy.policy_id}:{index}", "kind": "policy",
                        "title": policy.policy_type, "shop_id": shop_id, "source_type": "policy",
                        "embedding_text": embedding_text,
                        "keywords": embedding_text,
                    },
                ))
        published = db.query(KnowledgeDocument, KnowledgeVersion).join(
            KnowledgeVersion,
            (KnowledgeVersion.document_id == KnowledgeDocument.document_id) &
            (KnowledgeVersion.version == KnowledgeDocument.published_version),
        ).filter(KnowledgeDocument.shop_id == shop_id).all()
        for document, version in published:
            for index, chunk in enumerate(split_policy(version.content)):
                embedding_text = f"{document.title} {document.category} {chunk}"
                documents.append(Document(
                    page_content=chunk,
                    metadata={
                        "id": f"knowledge:{document.document_id}:{version.version}:{index}",
                        "kind": "policy", "title": document.title, "shop_id": shop_id,
                        "source_type": "knowledge", "version": version.version,
                        "locale": document.locale, "category": document.category,
                        "embedding_text": embedding_text, "keywords": embedding_text,
                    },
                ))
        return documents

    def documents(self, shop_id: str, kind: str | None = None, plan: QueryPlan | None = None) -> list[Document]:
        with transaction() as db:
            documents: list[Document] = []
            if kind != "policy":
                documents.extend(self._product_documents(db, shop_id, plan))
            if kind != "product":
                documents.extend(self._policy_documents(db, shop_id))
        return documents

    # ------------------------------------------------------------------
    # Dense index lifecycle
    # ------------------------------------------------------------------
    def _index_fingerprint(self, documents: Iterable[Document]) -> str:
        digest = hashlib.sha256()
        for document in sorted(documents, key=lambda item: item.metadata["id"]):
            digest.update(document.metadata["id"].encode())
            digest.update(document.metadata["embedding_text"].encode())
        return digest.hexdigest()

    def _warm(self, shop_id: str, documents: list[Document], fingerprint: str) -> None:
        try:
            if self.embedding is None:
                from langchain_huggingface import HuggingFaceEmbeddings
                self.embedding = HuggingFaceEmbeddings(
                    model_name=settings.embedding_model,
                    model_kwargs={"local_files_only": not settings.embedding_download, "device": "cpu"},
                    encode_kwargs={"normalize_embeddings": True},
                )
            vectors = self.embedding.embed_documents([document.metadata["embedding_text"] for document in documents])
            with self.lock:
                self.indexes[shop_id] = (fingerprint, {document.metadata["id"]: vector for document, vector in zip(documents, vectors)})
                self.dense_status = "ready"
        except Exception:
            log.warning("Dense index unavailable; lexical RAG remains active. "
                        "Set EMBEDDING_ALLOW_DOWNLOAD=true to download the embedding model.")
            with self.lock:
                self.failed = True
                self.dense_status = "unavailable"
        finally:
            with self.lock:
                self.loading = False

    def warm(self, shop_id: str):
        documents = self.documents(shop_id)
        fingerprint = self._index_fingerprint(documents)
        with self.lock:
            current = self.indexes.get(shop_id)
            should_start = bool(documents) and self.dense_enabled and not self.loading and not self.failed and (
                not current or current[0] != fingerprint
            )
            if should_start:
                self.loading = True
                self.dense_status = "indexing"
                threading.Thread(target=self._warm, args=(shop_id, documents, fingerprint), daemon=True).start()
        return current if current and current[0] == fingerprint else None

    def invalidate(self, shop_id: str | None = None) -> None:
        """Explicit refresh for admin publish/rollback or a failed model load."""
        with self.lock:
            if shop_id:
                self.indexes.pop(shop_id, None)
            else:
                self.indexes.clear()
            self.failed = False
            self.dense_status = "not_started" if self.dense_enabled else "disabled"

    # ------------------------------------------------------------------
    # Ranking helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    @staticmethod
    def _title_coverage(title_terms: set[str], query_terms: set[str]) -> float:
        return len(title_terms & query_terms) / len(title_terms) if title_terms else 0.0

    def search(self, query: str, shop_id: str, kind: str | None = None, limit: int = 5) -> list[dict]:
        plan = analyse_query(query)
        if not plan.terms and not (
            plan.colors or plan.size or plan.max_price is not None or plan.min_price is not None
            or plan.in_stock is not None or plan.category
        ):
            return []
        documents = self.documents(shop_id, kind, plan)
        if not documents:
            return []
        index = self.warm(shop_id) if self.dense_enabled else None
        query_terms = set(plan.terms)
        corpora = [_token_list(document.metadata["keywords"]) for document in documents]
        document_frequency = Counter(term for corpus in corpora for term in set(corpus))
        total_documents = len(documents)
        average_length = sum(len(corpus) for corpus in corpora) / max(1, total_documents)
        lexical_scores: dict[str, float] = {}
        lexical_confidence: dict[str, float] = {}

        for document, corpus in zip(documents, corpora):
            counts = Counter(corpus)
            title_terms = set(_token_list(document.metadata["title"]))
            matched_terms = query_terms & set(corpus)
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if not frequency:
                    continue
                inverse_document_frequency = math.log(
                    1 + (total_documents - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
                )
                denominator = frequency + 1.5 * (0.25 + 0.75 * len(corpus) / max(average_length, 1))
                score += inverse_document_frequency * frequency * 2.5 / denominator
            title_overlap = len(title_terms & query_terms)
            score += 2.2 * title_overlap
            if title_terms and title_terms.issubset(query_terms):
                score += 8 * len(title_terms)
            if document.metadata["title"].casefold() in plan.normalised.casefold():
                score += 6
            if document.metadata.get("kind") == "product":
                if any(group & query_terms and group & title_terms for group in TYPE_GROUPS):
                    score += 4
                if document.metadata.get("data", {}).get("product_id", "").casefold() in plan.normalised.casefold():
                    score += 10
            else:
                for term, title in POLICY_DEFAULTS.items():
                    if term in query_terms and document.metadata["title"] == title:
                        score += 3
            if score <= 0:
                continue
            key = document.metadata["id"]
            lexical_scores[key] = score
            coverage = len(matched_terms) / max(1, len(query_terms))
            title_coverage = self._title_coverage(title_terms, query_terms)
            exact_topic = bool(title_terms and title_terms.issubset(query_terms))
            type_match = any(group & query_terms and group & title_terms for group in TYPE_GROUPS)
            lexical_confidence[key] = min(0.99, max(
                coverage,
                title_coverage * 0.9,
                0.9 if exact_topic else 0.0,
                0.8 if type_match else 0.0,
            ))

        # Attribute-only requests such as "out of stock products" or "products
        # under $30" carry no semantic term after stopword removal. The SQL filter
        # is the user's intent, so return the filtered rows deterministically.
        if not lexical_scores and not query_terms and (
            plan.colors or plan.size or plan.max_price is not None or plan.min_price is not None
            or plan.in_stock is not None or plan.category
        ):
            for document in documents:
                lexical_scores[document.metadata["id"]] = 1.0
                lexical_confidence[document.metadata["id"]] = 0.8

        dense_scores: dict[str, float] = {}
        if index and self.embedding:
            try:
                query_vector = self.embedding.embed_query(plan.normalised)
                for document in documents:
                    vector = index[1].get(document.metadata["id"])
                    if not vector:
                        continue
                    similarity = self._cosine(query_vector, vector)
                    if similarity >= self.dense_min_similarity:
                        dense_scores[document.metadata["id"]] = similarity
            except Exception:
                log.warning("Dense query failed; lexical results retained")

        fused: dict[str, float] = {}
        for weight, scores in ((1.2, lexical_scores), (1.0, dense_scores)):
            for rank, key in enumerate(sorted(scores, key=scores.get, reverse=True)):
                fused[key] = fused.get(key, 0.0) + weight / (60 + rank + 1)
        if not fused:
            return []

        max_lexical = max(lexical_scores.values(), default=1.0)
        filter_only = not query_terms and bool(
            plan.colors or plan.size or plan.max_price is not None or plan.min_price is not None
            or plan.in_stock is not None or plan.category
        )
        candidates = []
        for document in documents:
            key = document.metadata["id"]
            if key not in fused:
                continue
            data = document.metadata.get("data") or {}
            if document.metadata["kind"] == "product":
                if plan.groups and not any(group & query_terms and group & set(_token_list(document.metadata["title"])) for group in plan.groups):
                    continue
                title_terms = set(_token_list(document.metadata["title"]))
                if plan.groups and (title_terms & ACCESSORY_WORDS) and not (title_terms & ACCESSORY_WORDS & query_terms):
                    continue
                if plan.colors and not plan.colors.intersection(tokens(" ".join(data.get("colors", [])))):
                    continue
                if plan.size and plan.size.casefold() not in [size.casefold() for size in data.get("sizes", [])]:
                    continue
                if plan.max_price is not None and data.get("effective_price", 0) > plan.max_price:
                    continue
                if plan.min_price is not None and data.get("effective_price", 0) < plan.min_price:
                    continue
            lexical_score = lexical_scores.get(key, 0.0)
            dense_score = dense_scores.get(key, 0.0)
            title_terms = set(_token_list(document.metadata["title"]))
            coverage = len(query_terms & set(corpus := _token_list(document.metadata["keywords"]))) / max(1, len(query_terms))
            title_coverage = self._title_coverage(title_terms, query_terms)
            phrase = document.metadata["title"].casefold() in plan.normalised.casefold()
            exact_topic = bool(title_terms and title_terms.issubset(query_terms))
            type_match = any(group & query_terms and group & title_terms for group in TYPE_GROUPS)
            relevance = (
                0.45 * (lexical_score / max_lexical)
                + 0.25 * dense_score
                + 0.15 * coverage
                + 0.10 * max(title_coverage, 1.0 if phrase else 0.0)
                + 0.05 * (1.0 if (type_match or exact_topic) else 0.0)
            )
            confidence = max(lexical_confidence.get(key, 0.0), dense_score)
            if document.metadata["kind"] == "policy":
                # One generic word ("gift" in Gift Cards) must not turn into an
                # answer to an unrelated policy question ("gift wrapping").
                accepted = exact_topic or title_coverage >= 0.75 or coverage >= 0.60 or (
                    dense_score >= self.dense_min_similarity and coverage >= 0.34
                ) or dense_score >= 0.55
            else:
                # Lexical products need title/type/attribute evidence. A single
                # incidental description word must not match an unrelated query
                # such as "car loan" -> "Car vent" phone holder. Attribute-only
                # requests are already narrowed by SQL, so the filter is evidence.
                accepted = filter_only or (
                    exact_topic or phrase or type_match or title_coverage >= 0.5
                    or coverage >= 0.60 or dense_score >= self.dense_min_similarity
                )
            if not accepted:
                continue
            candidates.append({
                "document": document,
                "relevance": relevance,
                "confidence": confidence,
                "lexical_score": lexical_score,
                "dense_score": dense_score,
                "coverage": coverage,
                "retrieval": "hybrid" if key in lexical_scores and key in dense_scores else "dense" if key in dense_scores else "lexical",
            })

        candidates.sort(key=lambda item: (item["relevance"], item["confidence"], item["lexical_score"]), reverse=True)
        output = []
        title_counts: Counter[str] = Counter()
        for candidate in candidates:
            document = candidate["document"]
            title_key = document.metadata["title"]
            if document.metadata["kind"] == "policy":
                if title_counts[title_key] >= 2:
                    continue
                title_counts[title_key] += 1
            output.append({
                "id": document.metadata["id"],
                "kind": document.metadata["kind"],
                "title": document.metadata["title"],
                "content": document.page_content,
                "score": round(candidate["relevance"], 5),
                "confidence": round(candidate["confidence"], 3),
                "lexical_score": round(candidate["lexical_score"], 5),
                "dense_score": round(candidate["dense_score"], 5),
                "coverage": round(candidate["coverage"], 3),
                "data": document.metadata.get("data"),
                "version": document.metadata.get("version"),
                "source_type": document.metadata.get("source_type", document.metadata["kind"]),
                "retrieval": candidate["retrieval"],
            })
            if len(output) >= limit:
                break
        return output


retriever = HybridRetriever()
