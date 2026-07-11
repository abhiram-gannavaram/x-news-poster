#!/usr/bin/env python3
"""
Analyze news candidates with Amazon Bedrock (Claude Sonnet) and generate tweets.

Reads data/candidates.json, asks Claude to pick the best 1–2 stories and draft
engaging tweets, then writes data/tweets_to_post.json after a local quality gate.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Claude Sonnet 4.6 on Bedrock — requires inference profile (on-demand base ID is rejected)
# Override with BEDROCK_MODEL_ID if your account uses a different geo profile (eu./jp./global.)
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MAX_TWEET_LEN = 280
MIN_TWEET_LEN = 40


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        logger.error("Candidates file missing: %s — run fetch_news first", path)
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates") or []


def get_bedrock_client():
    """Create a Bedrock Runtime client from env / IAM credentials."""
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("bedrock-runtime", region_name=region)


def build_analysis_prompt(candidates: list[dict[str, Any]]) -> str:
    """Compact prompt listing candidates for Claude to rank and tweet-ify."""
    lines = []
    for i, c in enumerate(candidates, start=1):
        summary = (c.get("summary") or "")[:280]
        lines.append(
            f"{i}. [{c.get('source')}] {c.get('title')}\n"
            f"   URL: {c.get('url')}\n"
            f"   Summary: {summary}\n"
            f"   Category: {c.get('category')} | score: {c.get('relevance_score')}"
        )
    catalog = "\n\n".join(lines)

    return f"""You are an expert AI/Tech news curator and social media editor for X (Twitter).

TASK:
1. Read the candidate stories below.
2. Select the BEST 1 or 2 stories for an AI/Tech audience (prefer groundbreaking AI, major product launches, research breakthroughs, or high-signal industry news).
3. Skip clickbait, pure politics, celebrity gossip, and low-signal rumor.
4. For each selected story, write ONE engaging tweet.

TWEET RULES:
- Maximum 280 characters TOTAL including the URL and spaces.
- Lead with the hook; be clear and punchy; no clickbait.
- Include the full article URL at the end.
- Optional: 1–2 relevant hashtags max (#AI #MachineLearning etc.) only if they fit under 280.
- No emojis overload (0–2 max). No ALL CAPS shouting. No "BREAKING!!!" spam.
- Do not invent facts not present in the title/summary.
- Prefer original wording over copying the headline verbatim.

OUTPUT FORMAT — respond with ONLY valid JSON (no markdown fences):
{{
  "selections": [
    {{
      "candidate_index": <1-based index from the list>,
      "title": "<original title>",
      "url": "<article url>",
      "source": "<source name>",
      "why_selected": "<one short sentence>",
      "tweet": "<full tweet text including URL, <= 280 chars>"
    }}
  ],
  "skipped_reason_if_empty": "<if zero selections, explain why>"
}}

Select 1 story if only one is excellent; select 2 only if both are high quality.
If nothing is worth posting, return "selections": [].

CANDIDATES:
{catalog}
"""


def invoke_claude(
    prompt: str,
    *,
    model_id: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.4,
) -> str:
    """
    Call Claude via Bedrock Messages API (anthropic.claude-* models).

    Returns the assistant text content.
    """
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
    """Parse JSON from model output, tolerating accidental markdown fences."""
    cleaned = text.strip()
    # Strip ```json ... ``` if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    # Fallback: first { ... last }
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def quality_check_tweet(tweet: str, url: str) -> tuple[bool, str]:
    """
    Local quality gate before anything is queued for posting.

    Returns (ok, reason).
    """
    if not tweet or not tweet.strip():
        return False, "empty tweet"

    text = tweet.strip()
    length = len(text)

    if length > MAX_TWEET_LEN:
        return False, f"too long ({length} > {MAX_TWEET_LEN})"
    if length < MIN_TWEET_LEN:
        return False, f"too short ({length} < {MIN_TWEET_LEN})"

    # Prefer that the article URL is present (t.co will shorten on X)
    if url and url not in text:
        # Allow if domain appears without scheme
        domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        if domain and domain not in text:
            return False, "article URL missing from tweet"

    # Block obvious spam / low quality patterns
    banned = [
        r"(?i)\bclick here\b",
        r"(?i)\bfollow for more\b",
        r"(?i)\bsponsored\b",
        r"(?i)\bnft\s*giveaway\b",
        r"🔥{3,}",
        r"!{4,}",
    ]
    for pattern in banned:
        if re.search(pattern, text):
            return False, f"banned pattern: {pattern}"

    # Too many hashtags
    hashtags = re.findall(r"#\w+", text)
    if len(hashtags) > 3:
        return False, f"too many hashtags ({len(hashtags)})"

    return True, "ok"


def truncate_tweet_safely(tweet: str, url: str, max_len: int = MAX_TWEET_LEN) -> str:
    """
    If Claude slightly overshot, trim body text while keeping the URL.

    Returns empty string if even a hard trim cannot fit.
    """
    tweet = tweet.strip()
    if len(tweet) <= max_len:
        return tweet

    if url and url in tweet:
        body = tweet.replace(url, "").strip()
        # Leave room for space + url
        budget = max_len - len(url) - 1
        if budget < 20:
            return ""
        body = body[: budget - 1].rstrip(" ,;-…") + "…"
        return f"{body} {url}"

    return tweet[: max_len - 1].rstrip() + "…"


def analyze_and_generate(
    candidates: list[dict[str, Any]] | None = None,
    *,
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Run Claude analysis + tweet generation + quality checks.

    Returns list of approved tweet payloads ready for posting.
    """
    if candidates is None:
        candidates = load_candidates()

    if not candidates:
        logger.warning("No candidates to analyze.")
        return []

    # Cap what we send to the model for cost/latency
    batch = candidates[:25]
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
    for sel in selections[:2]:
        idx = sel.get("candidate_index")
        title = (sel.get("title") or "").strip()
        url = (sel.get("url") or "").strip()
        source = (sel.get("source") or "").strip()
        why = (sel.get("why_selected") or "").strip()
        tweet = (sel.get("tweet") or "").strip()

        # Backfill from candidate list if model omitted fields
        if isinstance(idx, int) and 1 <= idx <= len(batch):
            base = batch[idx - 1]
            title = title or base.get("title", "")
            url = url or base.get("url", "")
            source = source or base.get("source", "")

        if not tweet or not url:
            logger.warning("Skipping incomplete selection: %s", sel)
            continue

        # Soft repair for length
        if len(tweet) > MAX_TWEET_LEN:
            repaired = truncate_tweet_safely(tweet, url)
            if repaired:
                logger.info("Truncated tweet from %d → %d chars", len(tweet), len(repaired))
                tweet = repaired

        ok, reason = quality_check_tweet(tweet, url)
        if not ok:
            logger.warning("Quality check failed (%s): %s", reason, tweet[:120])
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
