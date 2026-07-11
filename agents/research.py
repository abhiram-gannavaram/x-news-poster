#!/usr/bin/env python3
"""
Deep-dive research on top RSS candidates.

1. Rank candidates (recency + AI signal + blocklist).
2. Fetch article HTML and extract readable text.
3. Use Claude to pull ONLY verified facts / skip weak PR.
4. Write data/research_brief.json for the writer + validator.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.bedrock_client import extract_json_safe, invoke_claude  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("research")

CANDIDATES_PATH = ROOT / "data" / "candidates.json"
BRIEF_PATH = ROOT / "data" / "research_brief.json"

# How many stories to deep-read each run
TOP_N = 6
MAX_PAGE_CHARS = 6000
REQUEST_TIMEOUT = 12

BLOCKLIST = (
    "sponsored",
    "webinar",
    "register now",
    "sign up now",
    "tickets on sale",
    "coupon",
    "black friday",
    "cyber monday",
    "wordle",
    "horoscope",
    "celebrity",
    "gossip",
    "best deals",
    "how to watch",
    "trailer",
    "giveaway",
    "discount code",
    "we are hiring",
    "job posting",
)

LOW_VALUE = (
    "raises $",
    "raised $",
    "funding round",
    "series a",
    "series b",
    "series c",
    "seed round",
    "acquires",
    "acquisition of",
)


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        logger.error("Missing %s — run fetch_news first", path)
        return []
    with path.open(encoding="utf-8") as f:
        return (json.load(f).get("candidates") or [])


def is_blocked(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(p in text for p in BLOCKLIST)


def research_score(item: dict[str, Any]) -> float:
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    source = (item.get("source") or "").lower()
    if is_blocked(title, summary):
        return -999.0
    text = f"{title} {summary}".lower()
    score = float(item.get("relevance_score") or 0)
    for p in LOW_VALUE:
        if p in text:
            score -= 3.0
    # Reddit discussion threads are usually low signal for fact-checked posts
    if "reddit" in source:
        score -= 4.0
        if re.search(r"\[d\]|\[p\]|\[r\]|\[n\]", title, re.I):
            score -= 6.0
    age = item.get("age_hours")
    if isinstance(age, (int, float)):
        if age <= 12:
            score += 4.0
        elif age <= 24:
            score += 2.5
        elif age <= 48:
            score += 1.0
        else:
            score -= 3.0
    return score


def strip_html(html: str) -> str:
    text = unescape(html or "")
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_article_text(url: str) -> str:
    """Best-effort page fetch. Returns empty string on failure."""
    if not url:
        return ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; x-news-poster-research/1.0; +https://github.com/x-news-poster)"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            logger.warning("HTTP %s for %s", resp.status_code, url)
            return ""
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "text" not in ctype and ctype:
            return ""
        return strip_html(resp.text)[:MAX_PAGE_CHARS]
    except requests.RequestException as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return ""


def pick_top(candidates: list[dict[str, Any]], n: int = TOP_N) -> list[dict[str, Any]]:
    ranked = []
    for c in candidates:
        s = research_score(c)
        if s <= -100:
            continue
        ranked.append((s, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in ranked[:n]]


def extract_facts_with_claude(item: dict[str, Any], body: str) -> dict[str, Any]:
    """
    Extract grounded facts. Reject thin PR / unverifiable noise.
    """
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    source = item.get("source") or ""
    url = item.get("url") or ""
    age = item.get("age_hours")

    body_clip = body[:4500] if body else "(page body unavailable — use title/summary carefully)"

    prompt = f"""You are a careful research analyst for an engineer who posts on X.

SOURCE
- Source name: {source}
- Title: {title}
- URL: {url}
- Age hours: {age}
- RSS summary: {summary}
- Page text (may be partial/noisy):
\"\"\"
{body_clip}
\"\"\"

TASK
1. Decide if this is worth an insight post for AI/tech builders (not PR fluff, funding vanity, gadget sales).
2. Extract ONLY facts clearly supported by the source text. No speculation.
3. Note what is unknown / not confirmed.
4. Propose a sharp angle a human engineer might care about (not a tweet yet).

RULES
- Prefer concrete: who did what, what changed, numbers, constraints, tradeoffs.
- Mark confidence low if page body was empty and only headline exists.
- If story is weak/stale/PR, set worth_posting=false.

Return ONLY JSON:
{{
  "worth_posting": true/false,
  "confidence": "high"|"medium"|"low",
  "verified_facts": ["fact1", "fact2"],
  "unknowns": ["what we cannot confirm"],
  "builder_angle": "one sentence angle for a human take",
  "skip_reason": "empty if worth_posting else why skip",
  "entity_names": ["companies/products/people mentioned"]
}}
"""
    raw = invoke_claude(prompt, max_tokens=900, temperature=0.15)
    result = extract_json_safe(
        raw,
        {
            "worth_posting": False,
            "confidence": "low",
            "verified_facts": [],
            "unknowns": ["parse failure"],
            "builder_angle": "",
            "skip_reason": "research JSON parse failure",
            "entity_names": [],
        },
    )
    return result


def run_research(candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if candidates is None:
        candidates = load_candidates()
    if not candidates:
        brief = {
            "researched_at": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "count": 0,
        }
        save_brief(brief)
        return brief

    top = pick_top(candidates)
    logger.info("Deep-diving %d candidate(s)", len(top))

    items: list[dict[str, Any]] = []
    for i, cand in enumerate(top, 1):
        url = cand.get("url") or ""
        logger.info("[%d/%d] Research %s", i, len(top), cand.get("title", "")[:80])
        body = fetch_article_text(url)
        logger.info("  page chars=%d domain=%s", len(body), urlparse(url).netloc)

        analysis = extract_facts_with_claude(cand, body)
        if not analysis.get("worth_posting"):
            logger.info("  skip: %s", analysis.get("skip_reason") or "not worth posting")
            continue
        if len(body) < 400 and analysis.get("confidence") != "high":
            logger.info("  skip: thin page body (%d chars) and not high confidence", len(body))
            continue
        if analysis.get("confidence") == "low":
            logger.info("  skip: low confidence research")
            continue
        facts = analysis.get("verified_facts") or []
        if len(facts) < 2:
            logger.info("  skip: fewer than 2 verified facts")
            continue

        items.append(
            {
                "title": cand.get("title"),
                "url": url,
                "source": cand.get("source"),
                "published": cand.get("published"),
                "age_hours": cand.get("age_hours"),
                "category": cand.get("category"),
                "rss_summary": cand.get("summary"),
                "page_chars": len(body),
                "confidence": analysis.get("confidence"),
                "verified_facts": facts,
                "unknowns": analysis.get("unknowns") or [],
                "builder_angle": analysis.get("builder_angle") or "",
                "entity_names": analysis.get("entity_names") or [],
            }
        )
        logger.info(
            "  kept confidence=%s facts=%d angle=%s",
            analysis.get("confidence"),
            len(facts),
            (analysis.get("builder_angle") or "")[:80],
        )

    brief = {
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }
    save_brief(brief)
    return brief


def save_brief(brief: dict[str, Any], path: Path = BRIEF_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(brief, f, indent=2, ensure_ascii=False)
    logger.info("Wrote research brief (%d items) → %s", brief.get("count", 0), path)


def main() -> int:
    brief = run_research()
    if not brief.get("items"):
        logger.warning("No research items survived filters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
