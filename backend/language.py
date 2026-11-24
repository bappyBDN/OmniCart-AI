"""Deterministic Bangla/Banglish normalization before any model or embedding call."""
import re
import unicodedata
from dataclasses import dataclass


BANGLISH = {
    "lal": "red", "laal": "red", "kalo": "black", "kala": "black", "nil": "blue", "neel": "blue",
    "shobuj": "green", "sobuj": "green", "shada": "white", "sada": "white", "holud": "yellow",
    "jamdani": "jamdani", "shari": "saree", "sharee": "saree", "sari": "saree",
    "jama": "shirt", "tshart": "tshirt", "tshirt": "tshirt", "juta": "shoe", "juto": "shoe",
    "kapor": "apparel", "suti": "cotton", "sutir": "cotton", "sutii": "cotton",
    "dam": "price", "daam": "price", "dham": "price", "koto": "", "kotoo": "", "ta": "", "ti": "",
    "ache": "", "ase": "", "achen": "", "hobe": "", "pabo": "", "dekhate": "", "dekhan": "",
    "chai": "", "chay": "", "amar": "", "amake": "", "apnader": "", "ekta": "", "er": "", "ki": "",
    "moddhe": "under", "vitore": "inside", "bhitore": "inside", "baire": "outside",
    "delivery": "delivery", "delivari": "delivery", "ferot": "return", "ferat": "return",
    "refund": "refund", "taka": "", "manush": "human", "manusher": "human", "agent": "agent",
    "dhonnobad": "thanks", "dhanyabad": "thanks", "assalamualaikum": "hello",
    "medium": "size M", "large": "size L", "small": "size S",
    "eta": "this", "ata": "this", "eita": "this", "aita": "this", "oita": "that",
    "etar": "its", "atar": "its", "eitar": "its", "aitar": "its",
    "keno": "why", "kano": "why", "khabo": "consume", "khawa": "consume",
    "khেতে": "consume", "khete": "consume", "nibo": "take", "nebo": "take",
    "kinbo": "buy", "kinte": "buy", "korte": "", "bolo": "tell", "bolen": "tell",
}
MARKERS = {"lal", "laal", "kalo", "nil", "neel", "shari", "sharee", "jama", "juta", "suti", "sutir",
           "dam", "daam", "dham", "koto", "kotoo", "ache", "ase", "hobe", "pabo", "chai", "amar", "amake", "apnader", "ferot", "dhonnobad", "manush", "manusher", "kotha",
           "eta", "ata", "eita", "aita", "oita", "etar", "atar", "eitar", "aitar", "keno", "kano",
           "khabo", "khawa", "khete", "nibo", "nebo", "kinbo", "kinte", "korte", "bolo", "bolen", "taka", "tk"}


@dataclass(frozen=True)
class NormalizedQuery:
    original: str
    text: str
    locale: str


def normalize_query(value):
    original = unicodedata.normalize("NFKC", value)
    lowered = original.casefold().translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    words = set(re.findall(r"[a-z]+", lowered))
    locale = "bn" if re.search(r"[\u0980-\u09ff]", lowered) else "bn-Latn" if words & MARKERS else "en"
    # Replace complete words, never substrings (e.g. 'dam' must not alter 'damaged').
    result = re.sub(r"\b[a-z]+\b", lambda m: BANGLISH.get(m[0], m[0]) if locale == "bn-Latn" else m[0], lowered)
    return NormalizedQuery(original, " ".join(result.split()), locale)


def is_bangla(value):
    return normalize_query(value).locale.startswith("bn")
