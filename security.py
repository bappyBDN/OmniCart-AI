# security.py

"""

Security firewall — validates user input before it reaches the AI agents.

Protects against: prompt injection, excessive length, forbidden content.

"""

import re

from typing import Tuple

from loguru import logger



# ─────────────────────────────────────────

# Config

# ─────────────────────────────────────────

MAX_MESSAGE_LENGTH = 2000  # characters



# Prompt injection attack patterns

INJECTION_PATTERNS = [

    r"ignore (all |previous |prior |above |your )?(instructions?|rules?|prompts?|system)",

    r"(you are now|act as|pretend to be|roleplay as|simulate being)",

    r"(forget|disregard|override|bypass|jailbreak)",

    r"(system prompt|base prompt|initial prompt|your prompt)",

    r"(developer mode|dan mode|unrestricted mode)",

    r"(<system>|<\/system>|<instructions>|<\/instructions>)",

    r"(\[INST\]|\[\/INST\]|<<SYS>>|<</SYS>>)",    # common jailbreak tokens

]



# Compiled patterns for performance

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]





def security_firewall(message: str) -> Tuple[bool, str]:

    """

    Validate an incoming user message.



    Returns:

        (True, "OK")            — message is safe, proceed

        (False, reason_string)  — message blocked, reason given

    """

    if not message or not message.strip():

        return False, "Empty message received."



    # 1. Length check

    if len(message) > MAX_MESSAGE_LENGTH:

        logger.warning(f"[Security] Message too long: {len(message)} chars")

        return False, (

            f"Your message is too long ({len(message)} characters). "

            f"Please keep it under {MAX_MESSAGE_LENGTH} characters."

        )



    # 2. Prompt injection check

    for pattern in _COMPILED_PATTERNS:

        if pattern.search(message):

            logger.warning(f"[Security] Injection attempt blocked: '{message[:80]}...'")

            return False, (

                "Your message contains content that cannot be processed. "

                "Please ask a genuine shopping-related question."

            )



    # 3. Repeated character spam (e.g. "aaaaaaaaaa...")

    if re.search(r"(.)\1{30,}", message):

        return False, "Your message appears to contain spam. Please try again."



    return True, "OK"





def sanitize_input(text: str) -> str:

    """

    Light sanitization — strip leading/trailing whitespace

    and collapse multiple spaces into one.

    Does NOT remove special chars since users write in Bangla.

    """

    if not text:

        return ""

    text = text.strip()

    text = re.sub(r"[ \t]{3,}", "  ", text)   # collapse runs of spaces/tabs

    return text