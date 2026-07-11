#!/usr/bin/env python3
"""
Turn fresh AI/Tech signal into short insight posts (not news headlines + links).

Reads data/candidates.json, uses Claude Sonnet on Bedrock to extract a sharp take,
then writes data/tweets_to_post.json after a strict quality gate matching the
account's natural voice.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("analyze_and_generate")

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = ROOT / "data" / "candidates.json"
TWEETS_PATH = ROOT / "data" / "tweets_to_post.json"

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MAX_TWEET_LEN = 280
# Prefer short complete takes like the account's real posts (~120–240 chars)
SOFT_MAX_LEN = 240
MIN_TWEET_LEN = 60
# One strong post per run beats two half-baked ones
MAX_POSTS_PER_RUN = 1


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        logger.error("Candidates file missing: %s — run fetch_news first", path)
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates") or []


def get_bedrock_client():
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    return boto3.client("bedrock-runtime", region_name=region)


def build_analysis_prompt(candidates: list[dict[str, Any]]) -> str:
    """Prompt that produces insight posts in the account's voice — not link dumps."""
    lines = []
    for i, c in enumerate(candidates, start=1):
        summary = (c.get("summary") or "")[:220]
        age = c.get("age_hours")
        age_s = f"{age:.1f}h ago" if isinstance(age, (int, float)) else "unknown age"
        lines.append(
            f"{i}. [{c.get('source')}] ({age_s}) {c.get('title')}\n"
            f"   Summary: {summary}\n"
            f"   URL (internal only, DO NOT put in tweet): {c.get('url')}"
        )
    catalog = "\n\n".join(lines)

    return f"""You write short X posts for an engineer who thinks in public about AI and software.

VOICE (study these real examples — match this energy exactly):

- "Daily LLM use quietly rewires how you solve problems. The shortcut becomes the default path and suddenly your own reasoning feels slow and unreliable."
- "Started rejecting AI-written commit messages on my team. They read clean but erase the messy context that explains why a change actually happened, and that context is what you need six months later."
- "Most SWE-Bench wins aren't measuring new capability anymore. They're measuring how much of the benchmark leaked into training data. Once that line blurs, the scores stop predicting real engineering performance."
- "The next constraint isn't model quality. It's how much it costs to serve at scale on mixed hardware. French teams releasing free inference layers just made that cheaper."
- "Agents that run background tasks without supervision shift the bottleneck from intelligence to orchestration. Gemini's update quietly highlights how few teams are set up for that yet."

WHAT YOU DO:
1. Read the candidate stories (signal only — not copy to rewrite).
2. Pick the SINGLE freshest, highest-signal item with a real insight for builders.
3. Prefer items from the last 24–48 hours. Skip older recycled product news.
4. Write ONE complete insight post inspired by that signal — not a press release.

HARD RULES FOR THE POST:
- NO links / URLs / domains
- NO hashtags
- NO emojis
- NO "just launched", "excited to share", "BREAKING", "check this out", "thread"
- NO trailing ellipsis (…) or cut-off mid-sentence — the thought must land clean
- NO em-dash spam; one short dash is fine if natural
- NO marketing voice ("game-changer", "revolutionary", "unlock")
- Max 240 characters (hard ceiling 280). Ideal: 120–220.
- Complete sentences. Sound like a person who ships software, not a news bot.
- Distill a take / implication / tradeoff. Name the company or product only if it earns the line.
- Do not invent facts; stay grounded in the candidate.

BANNED SHAPES:
- "X just launched Y — a Z that does W. Built in N days…"
- "Company announces product. Here's why it matters: …"
- Anything that needs a link to make sense
- Anything truncated with …

OUTPUT — ONLY valid JSON (no markdown fences):
{{
  "selections": [
    {{
      "candidate_index": <1-based index>,
      "title": "<source title for history>",
      "url": "<source url for history only>",
      "source": "<source name>",
      "why_selected": "<one short sentence>",
      "tweet": "<complete insight post, no URL, no hashtags>"
    }}
  ],
  "skipped_reason_if_empty": "<if nothing is sharp enough>"
}}

Return exactly 1 selection, or []. Never return 2.
If every candidate is stale PR noise, return [].

CANDIDATES:
{catalog}
"""


def invoke_claude(
    prompt: str,
    *,
    model_id: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.55,
) -> str:
    model_id = model_id or os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
    client = get_bedrock_client()

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
    }

    logger.info("Invoking Bedrock model %s …", model_id)
    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error("Bedrock invoke failed: %s", exc)
        raise

    payload = json.loads(response["body"].read())
    parts = payload.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text.strip():
        raise RuntimeError(f"Empty Bedrock response: {payload!r}")
    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


_URL_RE = re.compile(r"https?://|www\.|\b\w+\.(com|ai|io|org|net|co)/\S*", re.I)
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)\s*$")
_MID_CUT_RE = re.compile(r"(\.\.\.|…)\s*$|,\s*$|—\s*$|-\s*$")
_PR_RE = re.compile(
    r"(?i)\b("
    r"just launched|just announced|excited to|game[- ]?changer|revolutionary|"
    r"breaking|check (this|it) out|thread:|link in bio|must[- ]read|"
    r"unlock(s|ing)?|delighted to|proud to announce"
    r")\b"
)


def quality_check_tweet(tweet: str, url: str = "") -> tuple[bool, str]:
    """
    Strict gate: complete insight posts only — no links, no truncation, no PR spam.
    """
    if not tweet or not tweet.strip():
        return False, "empty tweet"

    text = tweet.strip()
    length = len(text)

    if length > MAX_TWEET_LEN:
        return False, f"too long ({length} > {MAX_TWEET_LEN})"
    if length > SOFT_MAX_LEN:
        return False, f"too long for voice ({length} > {SOFT_MAX_LEN})"
    if length < MIN_TWEET_LEN:
        return False, f"too short ({length} < {MIN_TWEET_LEN})"

    # Never ship truncated / incomplete lines
    if _ELLIPSIS_RE.search(text) or text.endswith("…"):
        return False, "ends with ellipsis (half-baked)"
    if text.endswith((",", ";", "—", "-", ":")):
        return False, "ends mid-thought"
    if not text[-1] in ".!?\"'":
        # Allow ending without period if last char is letter (short punchy line)
        if not text[-1].isalnum():
            return False, "awkward ending"

    if _URL_RE.search(text):
        return False, "contains URL/domain (insights only)"
    if url:
        domain = urlparse(url).netloc.replace("www.", "")
        if domain and domain.lower() in text.lower():
            return False, "contains source domain"

    if _HASHTAG_RE.search(text):
        return False, "hashtags not allowed"
    if re.search(r"[\U0001F300-\U0001FAFF]", text):
        return False, "emoji not allowed"
    if _PR_RE.search(text):
        return False, "PR / launch-speak"
    if text.count("—") + text.count("–") > 1:
        return False, "too many dashes"
    if text.isupper() and length > 20:
        return False, "all caps"

    # Avoid pure headline restates that read like a news bot
    if text.lower().startswith(
        ("anthropic just", "openai just", "google just", "meta just", "microsoft just")
    ):
        return False, "news-bot lead"

    return True, "ok"


def analyze_and_generate(
    candidates: list[dict[str, Any]] | None = None,
    *,
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    if candidates is None:
        candidates = load_candidates()

    if not candidates:
        logger.warning("No candidates to analyze.")
        return []

    # Prefer freshest first (fetch_news already sorts, but re-sort defensively)
    batch = sorted(
        candidates,
        key=lambda c: (
            c.get("age_hours") is None,
            c.get("age_hours") if c.get("age_hours") is not None else 9999,
            -(c.get("relevance_score") or 0),
        ),
    )[:20]

    prompt = build_analysis_prompt(batch)
    raw = invoke_claude(prompt, model_id=model_id)
    logger.info("Claude raw response length: %d chars", len(raw))

    try:
        parsed = extract_json(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Claude JSON: %s\nRaw:\n%s", exc, raw[:800])
        return []

    selections = parsed.get("selections") or []
    if not selections:
        logger.info(
            "Claude selected nothing. Reason: %s",
            parsed.get("skipped_reason_if_empty") or "n/a",
        )
        return []

    approved: list[dict[str, Any]] = []
    for sel in selections[:MAX_POSTS_PER_RUN]:
        idx = sel.get("candidate_index")
        title = (sel.get("title") or "").strip()
        url = (sel.get("url") or "").strip()
        source = (sel.get("source") or "").strip()
        why = (sel.get("why_selected") or "").strip()
        tweet = (sel.get("tweet") or "").strip()

        if isinstance(idx, int) and 1 <= idx <= len(batch):
            base = batch[idx - 1]
            title = title or base.get("title", "")
            url = url or base.get("url", "")
            source = source or base.get("source", "")

        if not tweet:
            logger.warning("Skipping empty tweet selection: %s", sel)
            continue

        # Never auto-truncate with "…" — rewrite fail is better than half-baked
        if len(tweet) > MAX_TWEET_LEN:
            logger.warning(
                "Rejecting over-long tweet (%d chars) instead of truncating: %s",
                len(tweet),
                tweet[:100],
            )
            continue

        ok, reason = quality_check_tweet(tweet, url)
        if not ok:
            logger.warning("Quality check failed (%s): %s", reason, tweet)
            continue

        approved.append(
            {
                "title": title,
                "url": url,
                "source": source,
                "why_selected": why,
                "tweet": tweet,
                "char_count": len(tweet),
                "quality_check": reason,
                "style": "insight",
            }
        )

    logger.info("Approved %d / %d selection(s)", len(approved), len(selections))
    return approved


def save_tweets(tweets: list[dict[str, Any]], path: Path = TWEETS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(tweets),
        "tweets": tweets,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %d tweet(s) → %s", len(tweets), path)


def main() -> int:
    tweets = analyze_and_generate()
    save_tweets(tweets)
    if not tweets:
        logger.warning("No tweets approved this run.")
        return 0
    for i, t in enumerate(tweets, 1):
        logger.info("Tweet %d (%d chars): %s", i, t["char_count"], t["tweet"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
