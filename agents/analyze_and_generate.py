#!/usr/bin/env python3
"""
Write ONE human insight draft from the research brief.

Not a news recap. No links. No AI voice. No underscores / em dashes.
Draft only — validate.py must approve before post_to_x runs.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.bedrock_client import extract_json_safe, invoke_claude  # noqa: E402

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
    if not path.exists():
        logger.error("Missing research brief %s — run research.py first", path)
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f).get("items") or []


def load_recent_posts(path: Path = HISTORY_PATH, n: int = 8) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            posted = json.load(f).get("posted") or []
        texts = []
        for item in posted[-n:]:
            if isinstance(item, dict) and item.get("tweet"):
                texts.append(item["tweet"])
        return texts
    except (json.JSONDecodeError, OSError):
        return []


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
4. Avoid decorative hyphen stacks like "built-in" chains of jargon; prefer plain words ("built in")
5. No trailing ellipsis … or "..."
6. No AI voice / corporate sludge: delve, landscape, leverage, robust, unlock, game changer,
   navigate, tapestry, crucial, seamless, cutting edge, in today's world, it's worth noting,
   furthermore, moreover, holistic, synergy, paradigm, revolutionize, excited to share
7. No news-bot leads: "just launched", "just announced", "BREAKING", "Company X unveiled"
8. Do not invent numbers, quotes, or motives not in verified facts
9. If facts are thin, return empty selections rather than padding
10. 70 to 220 characters preferred (hard max 240). Complete sentences that land. Sound like a person, not a model.

OUTPUT only JSON (no markdown fences):
{{
  "selections": [
    {{
      "research_index": <1-based index>,
      "title": "<source title>",
      "url": "<source url for history>",
      "source": "<source name>",
      "why_selected": "<one short sentence>",
      "grounding_notes": "<which facts the post uses>",
      "tweet": "<final post text>"
    }}
  ],
  "skipped_reason_if_empty": "<if none>"
}}

Return 0 or 1 selection only.
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
    if not selections:
        logger.info("Writer returned empty: %s", parsed.get("skipped_reason_if_empty"))
        return []

    drafts: list[dict[str, Any]] = []
    for sel in selections[:1]:
        idx = sel.get("research_index")
        title = (sel.get("title") or "").strip()
        url = (sel.get("url") or "").strip()
        source = (sel.get("source") or "").strip()
        tweet = (sel.get("tweet") or "").strip()
        why = (sel.get("why_selected") or "").strip()
        grounding = (sel.get("grounding_notes") or "").strip()

        base: dict[str, Any] = {}
        if isinstance(idx, int) and 1 <= idx <= len(items):
            base = items[idx - 1]
            title = title or base.get("title", "")
            url = url or base.get("url", "")
            source = source or base.get("source", "")

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
                "validation_approved": False,  # must pass validate.py
                "status": "draft",
            }
        )

    logger.info("Produced %d draft(s)", len(drafts))
    return drafts


def save_drafts(drafts: list[dict[str, Any]], path: Path = DRAFTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(drafts),
        "tweets": drafts,
        "note": "Drafts only. validate.py must set validation_approved=true before posting.",
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Wrote drafts → %s", path)


def main() -> int:
    drafts = generate_drafts()
    save_drafts(drafts)
    for i, d in enumerate(drafts, 1):
        logger.info("DRAFT %d (%d): %s", i, d["char_count"], d["tweet"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
