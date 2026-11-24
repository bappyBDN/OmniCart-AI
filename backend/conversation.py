"""Bounded conversational intent helpers; no model calls or invented business facts."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from backend.language import normalize_query


def currency_question(query):
    text = normalize_query(query).text + " " + query.casefold()
    return bool(re.search(r"\b(?:bdt|tk|taka|bangladeshi|bangladesh\s+currency|exchange\s+rate)\b|বাংলাদেশি|বাংলাদেশী|টাকায়|টাকায়|টাকাতে|বিনিময়\s*হার", text))


def supplied_exchange_rate(query):
    """Only accept an explicitly stated USD→BDT rate, not an unlabelled number."""
    text = unicodedata.normalize("NFKC", query).casefold().translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")).replace(",", "")
    match = re.search(
        r"(?:\b1\s*(?:usd|us\s*dollars?|dollars?)|১\s*ডলার|1\s*ডলার)\s*"
        r"(?:=|:|is|equals?|equal\s+to|মানে|সমান|হলে|ধরে)?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:bdt\b|tk\b|taka\b|টাকা)", text,
    )
    if not match:
        return None
    try:
        rate = Decimal(match.group(1))
        return rate if Decimal("0") < rate <= Decimal("10000") else None
    except InvalidOperation:
        return None


def purpose_question(query):
    text = normalize_query(query).text
    return bool(re.search(
        r"\bwhy\b|\bwhat\b.{0,30}\b(?:used\s+for|benefits?|purpose)\b|"
        r"\b(?:benefits?|dosage|consume|safe\s+for|suitable\s+for|should\s+i\s+(?:take|eat|use))\b|"
        r"কেন|কী\s*কাজ|কি\s*কাজ|উপকার|খাব|খেতে|সেবন|ডোজ|আমার\s*জন্য", text))


def purchase_request(query):
    text = normalize_query(query).text
    if purpose_question(query) or re.search(
        r"\b(?:don't|do\s+not|not\s+ready|cancel|cancellation|refund|return|exchange|track|status)\b|"
        r"\bhow\s+(?:do|can|to)\b|বাতিল|রিফান্ড|ফেরত|কিনব\s*না|নেব\s*না|চাই\s*না", text):
        return False
    return bool(re.search(
        r"\b(?:buy|purchase|order|take)\b|কিনতে|কিনব|নেব|নিব|অর্ডার\s*(?:কর|দে|চাই)", text))


def referential_question(query):
    text = normalize_query(query).text
    return bool(re.search(
        r"\b(?:it|this|that|its|they|them|their|these|those|one|color|colour|size|price|cost|stock|"
        r"available|availability|details|more|image|photo|picture)\b|"
        r"এটা|এটি|এটার|এটির|ওটা|ওটির|সেটা|রঙ|সাইজ|দাম|স্টক|বিস্তারিত|ছবি|একই", text)
        or currency_question(query) or purpose_question(query))


def alternative_request(query):
    text = normalize_query(query).text
    # Another attribute of the SAME product is not a request for another product.
    if re.search(r"\b(?:different|other|another)\s+(?:colou?rs?|sizes?|flavou?rs?)\b|অন্য\s*(?:রঙ|সাইজ|ফ্লেভার)", text):
        return False
    return bool(re.search(r"\b(?:alternatives?|similar|instead|other|else|compare|versus|vs)\b|বিকল্প|অন্য|তুলনা|পার্থক্য", text))


def product_choices(sources):
    result, seen = [], set()
    for source in sources or []:
        product_id = source.get("data", {}).get("product_id") if source.get("kind") == "product" else None
        if product_id and product_id not in seen:
            result.append(source)
            seen.add(product_id)
    return result


def selected_choice(query, sources):
    """Resolve an explicit name, SKU or ordinal; a bare 'this' never picks top-1."""
    choices = product_choices(sources)
    text = normalize_query(query).text
    explicit_ids = set(re.findall(r"\bsku_\d+\b", text))
    if explicit_ids:
        matches = [s for s in choices if s["data"]["product_id"].casefold() in explicit_ids]
    else:
        matches = [s for s in choices if re.search(rf"(?<!\w){re.escape(s['title'].casefold())}(?!\w)", text)]
    if len(matches) == 1:
        return matches[0]["data"]["product_id"]
    for index, pattern in enumerate((r"\bfirst\s+(?:one|product|item)\b|প্রথমটি|প্রথমটা", r"\bsecond\s+(?:one|product|item)\b|দ্বিতীয়টি|দ্বিতীয়টি|দ্বিতীয়টা")):
        if re.search(pattern, text) and index < len(choices):
            return choices[index]["data"]["product_id"]
    return None


def named_quantity(query):
    """Explicit purchase units, excluding weights, prices, dates and product sizes."""
    text = normalize_query(query).text
    match = re.search(r"(?<![\d.\-])([1-9]\d{0,2})\s*(?:packs?|packets?|pcs?|pieces?|units?|টি|টা|প্যাকেট)(?!\w)", text)
    if not match:
        match = re.search(r"\b(?:buy|order|purchase|take)\s+([1-9]\d{0,2})(?![\d.])\b", text)
    return int(match.group(1)) if match else None


def stated_name(query):
    match = re.fullmatch(r"\s*(?:my\s+name\s+is|name\s*[:=]|আমার\s*নাম|নাম\s*[:=])\s+([^\n]{2,120}?)\s*[.!।]?\s*", query, re.I)
    return match.group(1).strip() if match else None
