#!/usr/bin/env python3
"""
Write ONE human insight draft from the research brief.

Not a news recap. No links. No AI voice. No underscores / em dashes.
Draft only — validate.py must approve before post_to_x runs.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.bedrock_client import extract_json_safe, invoke_claude  # noqa: E402
from agents.utils import (  # noqa: E402
    atomic_write_json,
    coerce_index,
    load_history_strict,
    load_json_safe,
    normalize_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("analyze_and_generate")

BRIEF_PATH = ROOT / "data" / "research_brief.json"
DRAFTS_PATH = ROOT / "data" / "tweets_to_post.json"
HISTORY_PATH = ROOT / "data" / "posted_news.json"

MAX_TWEET_LEN = 240
MIN_TWEET_LEN = 70


def load_brief(path: Path = BRIEF_PATH) -> list[dict[str, Any]]:
    data = load_json_safe(path, {"items": []})
    if not isinstance(data, dict):
        return []
    items = data.get("items") or []
    return items if isinstance(items, list) else []


def load_recent_posts(path: Path = HISTORY_PATH, n: int = 8) -> list[str]:
    try:
        history = load_history_strict(path)
    except RuntimeError as exc:
        logger.error("%s", exc)
        # Fail closed for recency context only — do not invent empty if corrupt
        raise
    texts = []
    for item in (history.get("posted") or [])[-n:]:
        if isinstance(item, dict) and item.get("tweet"):
            texts.append(item["tweet"])
    return texts


def build_writer_prompt(items: list[dict[str, Any]], recent: list[str]) -> str:
    blocks = []
    for i, it in enumerate(items, 1):
        facts = "\n".join(f"   - {f}" for f in (it.get("verified_facts") or [])[:6])
        unknowns = ", ".join(it.get("unknowns") or []) or "none listed"
        blocks.append(
            f"{i}. [{it.get('source')}] age={it.get('age_hours')}h conf={it.get('confidence')}\n"
            f"   Title: {it.get('title')}\n"
            f"   Angle: {it.get('builder_angle')}\n"
            f"   Verified facts:\n{facts}\n"
            f"   Unknowns: {unknowns}\n"
            f"   URL (history only, NEVER put in post): {it.get('url')}"
        )
    catalog = "\n\n".join(blocks)
    recent_txt = "\n".join(f"- {t}" for t in recent) if recent else "None"

    return f"""You write short X posts for a working engineer who thinks in public about AI and software.

REAL VOICE EXAMPLES (match this energy — plain, concrete, complete thoughts):

- "Daily LLM use quietly rewires how you solve problems. The shortcut becomes the default path and suddenly your own reasoning feels slow and unreliable."
- "Started rejecting AI-written commit messages on my team. They read clean but erase the messy context that explains why a change actually happened, and that context is what you need six months later."
- "Most SWE-Bench wins aren't measuring new capability anymore. They're measuring how much of the benchmark leaked into training data. Once that line blurs, the scores stop predicting real engineering performance."
- "The next constraint isn't model quality. It's how much it costs to serve at scale on mixed hardware. French teams releasing free inference layers just made that cheaper."
- "Agents that run background tasks without supervision shift the bottleneck from intelligence to orchestration. Gemini's update quietly highlights how few teams are set up for that yet."

RESEARCHED STORIES (facts already verified — stay inside them):
{catalog}

ALREADY POSTED RECENTLY (do not repeat topic or angle):
{recent_txt}

TASK
Pick the single best researched story. Write ONE complete insight post grounded only in verified facts + a human take.

HARD RULES
1. No links, URLs, domains, hashtags, emojis
2. No underscore characters _
3. No em dash — or en dash – characters (use a period or plain comma instead)
4. Prefer plain words over jargon stacks
5. No trailing ellipsis … or "..."
6. No AI voice / corporate sludge: delve, landscape, leverage, robust, unlock, game changer,
   navigate, tapestry, crucial, seamless, cutting edge, in today's world, it's worth noting,
   furthermore, moreover, holistic, synergy, paradigm, revolutionize, excited to share
7. No news-bot leads: "just launched", "just announced", "BREAKING", "Company X unveiled"
8. Do not invent numbers, quotes, or motives not in verified facts
9. If facts are thin, return empty selections rather than padding
10. 70 to 220 characters preferred (hard max 240). Complete sentences that land. Sound like a person, not a model.

CONTENT SAFETY (must follow):
- Zero racism, casteism, xenophobia, or hate toward any race, religion, ethnicity, gender, or nationality
- No harassment, personal attacks, bullying, or dehumanizing language
- No violence glorification, self-harm content, or illegal harm instructions
- No toxic pile-ons; keep takes constructive and technical
- Skip stories that are only about politics/hate/violence — return empty selections instead

OUTPUT only JSON (no markdown fences):
{{
  "selections": [
    {{
      "research_index": <1-based index as integer>,
      "why_selected": "<one short sentence>",
      "grounding_notes": "<which facts the post uses>",
      "tweet": "<final post text>"
    }}
  ],
  "skipped_reason_if_empty": "<if none>"
}}

Return 0 or 1 selection only. Do not invent title/url — index only.
"""


def generate_drafts(items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if items is None:
        items = load_brief()
    if not items:
        logger.warning("No researched items to write from.")
        return []

    recent = load_recent_posts()
    prompt = build_writer_prompt(items, recent)
    raw = invoke_claude(prompt, max_tokens=900, temperature=0.55)
    logger.info("Writer raw length=%d", len(raw))

    parsed = extract_json_safe(
        raw,
        {"selections": [], "skipped_reason_if_empty": "writer JSON parse failure"},
    )
    selections = parsed.get("selections") or []
    if not isinstance(selections, list):
        selections = []
    if not selections:
        logger.info("Writer returned empty: %s", parsed.get("skipped_reason_if_empty"))
        return []

    drafts: list[dict[str, Any]] = []
    for sel in selections[:1]:
        if not isinstance(sel, dict):
            continue
        idx = coerce_index(sel.get("research_index"), len(items))
        if idx is None:
            logger.warning("Invalid research_index %r — skipping selection", sel.get("research_index"))
            continue

        base = items[idx - 1]
        # Prefer researched fields for history/dedup (never trust model URL)
        title = (base.get("title") or "").strip()
        url = normalize_url(base.get("url") or "")
        source = (base.get("source") or "").strip()
        tweet = (sel.get("tweet") or "").strip()
        why = (sel.get("why_selected") or "").strip()
        grounding = (sel.get("grounding_notes") or "").strip()

        if not tweet:
            continue

        drafts.append(
            {
                "title": title,
                "url": url,
                "source": source,
                "why_selected": why,
                "grounding_notes": grounding,
                "tweet": tweet,
                "char_count": len(tweet),
                "verified_facts": base.get("verified_facts") or [],
                "builder_angle": base.get("builder_angle") or "",
                "confidence": base.get("confidence"),
                "validation_approved": False,
                "status": "draft",
            }
        )

    logger.info("Produced %d draft(s)", len(drafts))
    return drafts


def save_drafts(drafts: list[dict[str, Any]], path: Path = DRAFTS_PATH) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(drafts),
        "tweets": drafts,
        "note": "Drafts only. validate.py must set validation_approved=true before posting.",
    }
    atomic_write_json(path, payload)
    logger.info("Wrote drafts → %s", path)


def main() -> int:
    try:
        drafts = generate_drafts()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    save_drafts(drafts)
    for i, d in enumerate(drafts, 1):
        logger.info("DRAFT %d (%d): %s", i, d["char_count"], d["tweet"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
