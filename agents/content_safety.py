"""
Content safety: block racism, hate, harassment, harmful content.

Constructive technical discussion is fine. Attacks on people/groups are not.
"""

from __future__ import annotations

import re
from typing import Any

# Hard blocklist (word-boundary). Keep focused; model does the rest.
_HARD_BLOCK = re.compile(
    r"(?i)\b("
    r"nazi|neonazi|white\s*power|kill\s*all|gas\s*the|"
    r"rape|lynch|genocide|"
    r"slur|go\s*back\s*to\s*your\s*country"
    r")\b"
)

# Topics that should not become posts (politics / identity attacks / violence)
_TOPIC_SKIP = (
    "racist",
    "racism",
    "racial slur",
    "hate speech",
    "ethnic cleansing",
    "white supremac",
    "antisemit",
    "islamophob",
    "homophob",
    "transphob",
    "mass shooting",
    "terrorist attack",
    "suicide method",
    "how to make a bomb",
)

CONTENT_POLICY = """
CONTENT SAFETY (non-negotiable):
- No racism, casteism, xenophobia, or hate against any race, religion, ethnicity, gender, or nationality
- No personal attacks, harassment, bullying, or doxxing
- No glorifying violence, self-harm, or illegal harm
- No sexual content involving minors; no explicit pornography
- No conspiracy hate / dehumanizing language
- No mean-spirited pile-ons on private individuals
- Constructive technical critique of products/systems is OK
- Political culture-war rants are NOT OK for this account
- If a story is only about harm, hate, or identity attacks, skip it entirely
"""


def hard_block_violations(text: str) -> list[str]:
    """Deterministic hard rejects. Empty list = ok."""
    if not text:
        return ["empty"]
    hits = _HARD_BLOCK.findall(text)
    if hits:
        return [f"blocked phrase: {h}" for h in hits[:3]]
    low = text.lower()
    for phrase in _TOPIC_SKIP:
        if phrase in low:
            return [f"blocked topic: {phrase}"]
    return []


def topic_is_unsafe(title: str, summary: str = "") -> bool:
    """True if research should skip this candidate."""
    blob = f"{title} {summary}".lower()
    if _HARD_BLOCK.search(blob):
        return True
    return any(p in blob for p in _TOPIC_SKIP)


def safety_check_prompt(tweet: str) -> str:
    return f"""You are a content safety reviewer for a public tech engineer's X account.

POST:
\"\"\"{tweet}\"\"\"

{CONTENT_POLICY}

Approve only if the post is safe for a general professional audience.

Respond ONLY JSON:
{{
  "safe": true/false,
  "categories": ["list any issues e.g. racism, hate, harassment, violence, harmful, personal_attack, toxic_criticism"],
  "feedback": "what failed, empty if safe"
}}

Rules:
- safe=false for racism, hate, harassment, violence glorification, or cruel personal attacks
- Technical critique of software/companies (e.g. "this API design fails under load") can be safe=true
- When unsure on hate/harm, set safe=false
"""


def parse_safety_result(result: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(result, dict):
        return False, "safety parse failure"
    if result.get("safe") is True and not result.get("categories"):
        return True, "ok"
    if result.get("safe") is True:
        # categories non-empty but safe true — still inspect
        cats = result.get("categories") or []
        bad = {
            "racism",
            "hate",
            "harassment",
            "violence",
            "harmful",
            "personal_attack",
            "toxic_criticism",
        }
        if any(str(c).lower() in bad for c in cats):
            return False, result.get("feedback") or str(cats)
        return True, "ok"
    return False, result.get("feedback") or "failed safety review"
