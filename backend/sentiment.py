"""Fast, deterministic sentiment + urgency classifier (English, Bangla, Banglish).

Runs on EVERY incoming message, so it must be cheap: no model call, no I/O.
Labels: POSITIVE, NEUTRAL, NEGATIVE, FRUSTRATED. The score is in [-1, 1].

The classifier is intentionally conservative. A mild complaint ("delivery was a
bit late") shifts the assistant's tone to empathetic; only clear anger or
repeated negativity is treated as escalation-worthy (see `needs_escalation`).
"""
import re
import unicodedata
from dataclasses import asdict, dataclass, field

POSITIVE = "POSITIVE"
NEUTRAL = "NEUTRAL"
NEGATIVE = "NEGATIVE"
FRUSTRATED = "FRUSTRATED"

_POSITIVE = (
    r"thanks?|thank you|thx|great|awesome|amazing|love(?:ly)?|perfect|excellent|nice|good|"
    r"happy|glad|wonderful|fantastic|helpful|beautiful|cool|super|best|satisfied|"
    r"ধন্যবাদ|ভালো|ভাল|চমৎকার|দারুণ|দারুন|সুন্দর|খুশি|অসাধারণ|"
    r"dhonnobad|valo|bhalo|darun|sundor|khushi"
)
_NEGATIVE = (
    r"bad|poor|late|delayed?|wrong|broken|damaged?|defective|torn|missing|disappointed|"
    r"disappointing|unhappy|not happy|problem|issue|complaint|slow|terrible|awful|"
    r"horrible|cheap quality|low quality|fake|never (?:arrived|came|received)|"
    r"not (?:received|delivered|working)|doesn'?t work|stopped working|"
    r"খারাপ|বাজে|দেরি|ভুল|নষ্ট|ভাঙা|ছেঁড়া|সমস্যা|অভিযোগ|পাইনি|আসেনি|হতাশ|"
    r"kharap|baje|deri|vul|nosto|somossa|paini|ashe ?ni"
)
# Strong signals: anger, accusations, exasperation, threats.
_FRUSTRATED = (
    r"useless|worst|ridiculous|pathetic|scam|scammer|fraud|cheat(?:ed|ing)?|liar|lying|"
    r"furious|angry|very angry|so angry|fed up|sick of|tired of|had enough|"
    r"waste of (?:time|money)|unacceptable|disgusting|nonsense|stupid|idiot|"
    r"how many times|again and again|still (?:not|no|waiting|haven'?t)|"
    r"no one (?:is )?(?:helping|responding|replying)|nobody (?:helps|replies)|"
    r"(?:legal action|consumer court|police|report you)|"
    r"প্রতারক|প্রতারণা|ফালতু|রাগ|বিরক্ত|জঘন্য|ধোঁকা|বাটপার|বারবার|আর কত|"
    r"faltu|batpar|birokto|dhoka|rag lagche|baje service"
)
_URGENT = (
    r"urgent(?:ly)?|asap|immediately|right now|right away|emergency|today itself|"
    r"within (?:an? )?hour|now please|জরুরি|এখনই|এক্ষুনি|তাড়াতাড়ি|joruri|ekhoni|taratari"
)
_NEGATION = r"(?:not|no|never|isn'?t|wasn'?t|don'?t|didn'?t|hardly|না|নয়|নেই)"
_INTENSIFIER = r"(?:very|so|really|extremely|totally|completely|absolutely|খুব|অনেক|ভীষণ|khub|onek)"

_POS_RE = re.compile(rf"(?<!\w)(?:{_POSITIVE})(?!\w)", re.I)
_NEG_RE = re.compile(rf"(?<!\w)(?:{_NEGATIVE})s?(?!\w)", re.I)
_FRU_RE = re.compile(rf"(?<!\w)(?:{_FRUSTRATED})s?(?!\w)", re.I)
_URG_RE = re.compile(rf"(?<!\w)(?:{_URGENT})(?!\w)", re.I)
_NEGATED_POS = re.compile(rf"(?<!\w){_NEGATION}\s+(?:\w+\s+)?(?:{_POSITIVE})(?!\w)", re.I)
_NEGATED_NEG = re.compile(rf"(?<!\w){_NEGATION}\s+(?:{_NEGATIVE})(?!\w)", re.I)
_INTENSE_NEG = re.compile(rf"(?<!\w){_INTENSIFIER}\s+(?:{_NEGATIVE})(?!\w)", re.I)
# Product-search phrasing that merely mentions a "negative" word, e.g.
# "is it damage resistant?", "not bad quality ones" or "good for late nights".
_BENIGN = re.compile(r"\b(?:resistant|proof|free|warranty against|protection|fake leather|faux)\b", re.I)


@dataclass
class Sentiment:
    label: str = NEUTRAL
    score: float = 0.0
    urgency: str = "normal"  # normal | high
    signals: list = field(default_factory=list)

    @property
    def negative(self):
        return self.label in (NEGATIVE, FRUSTRATED)

    def as_dict(self):
        return asdict(self)


def classify(text: str) -> Sentiment:
    """Lexicon + emphasis heuristics. Never raises; empty text is NEUTRAL."""
    raw = unicodedata.normalize("NFKC", text or "").strip()
    if not raw:
        return Sentiment()
    lowered = raw.casefold()
    signals, score = [], 0.0

    positives = len(_POS_RE.findall(lowered)) - len(_NEGATED_POS.findall(lowered))
    negatives = len(_NEG_RE.findall(lowered)) - len(_NEGATED_NEG.findall(lowered))
    frustrated = len(_FRU_RE.findall(lowered))
    negated_positive = len(_NEGATED_POS.findall(lowered))
    if _BENIGN.search(lowered) and not frustrated:
        negatives = max(0, negatives - 1)

    score += 0.35 * max(positives, 0)
    score -= 0.35 * max(negatives, 0) + 0.3 * negated_positive + 0.6 * frustrated
    if positives > 0:
        signals.append("positive_terms")
    if negatives > 0 or negated_positive:
        signals.append("negative_terms")
    if frustrated:
        signals.append("frustration_terms")
    if _INTENSE_NEG.search(lowered):
        score -= 0.25
        signals.append("intensified_negative")

    # Emphasis amplifies whatever polarity is already present.
    letters = [c for c in raw if c.isalpha() and c.isascii()]
    shouting = len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7
    exclaims = raw.count("!") >= 3 or "?!" in raw or "!?" in raw
    if shouting:
        signals.append("all_caps")
    if exclaims:
        signals.append("repeated_punctuation")
    if score < 0 and (shouting or exclaims):
        score -= 0.25
    elif score > 0 and exclaims:
        score += 0.1

    urgent = bool(_URG_RE.search(lowered))
    if urgent:
        signals.append("urgent_terms")
    score = max(-1.0, min(1.0, round(score, 2)))

    if frustrated or score <= -0.8:
        label = FRUSTRATED
    elif score <= -0.3:
        label = NEGATIVE
    elif score >= 0.3:
        label = POSITIVE
    else:
        label = NEUTRAL
    urgency = "high" if urgent or label == FRUSTRATED else "normal"
    return Sentiment(label=label, score=score, urgency=urgency, signals=signals)


def needs_escalation(sentiment: Sentiment, negative_streak: int = 0) -> bool:
    """Strong anger escalates immediately; mild negativity only when it persists.

    `negative_streak` counts consecutive NEGATIVE/FRUSTRATED customer turns
    *including* the current one.
    """
    if sentiment.label == FRUSTRATED:
        return True
    return sentiment.label == NEGATIVE and (sentiment.score <= -0.7 or negative_streak >= 3)


def tone_instruction(sentiment: Sentiment) -> str:
    """Prompt guidance injected into the reasoning context for upset customers."""
    if sentiment.label == FRUSTRATED:
        return ("CUSTOMER MOOD: frustrated. Open with one short, sincere apology that names "
                "their problem, skip all selling, and focus on the next concrete step.")
    if sentiment.label == NEGATIVE:
        return ("CUSTOMER MOOD: unhappy. Acknowledge the problem warmly in one short sentence "
                "before answering. Be calm, specific and solution-focused; no upselling.")
    return ""


def empathy_prefix(sentiment: Sentiment, bangla: bool = False) -> str:
    """Prefix for deterministic (rules-based) replies when the customer is upset."""
    if not sentiment.negative:
        return ""
    if bangla:
        return "অসুবিধার জন্য সত্যিই দুঃখিত। "
    return "I'm really sorry about the trouble. " if sentiment.label == FRUSTRATED else "Sorry about that. "


_APOLOGY = re.compile(r"^\s*(?:i'?m (?:so |really |very )?sorry|sorry|apologies|i apologi[sz]e|দুঃখিত|অসুবিধার জন্য)", re.I)


def already_apologetic(answer: str) -> bool:
    return bool(_APOLOGY.search(answer or ""))
