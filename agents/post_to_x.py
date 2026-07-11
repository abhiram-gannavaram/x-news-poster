#!/usr/bin/env python3
"""
Post ONLY validation-approved tweets to X (Twitter) via API v2.

Safety:
- Requires validation_approved=true on each item
- Final style recheck aligned with validate gates
- DRY_RUN=true never calls X, never writes history
- AUTO_POST=false (default) refuses live posts
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tweepy

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.content_safety import hard_block_violations  # noqa: E402
from agents.utils import (  # noqa: E402
    atomic_write_json,
    load_history_strict,
    load_json_safe,
    normalize_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("post_to_x")

TWEETS_PATH = ROOT / "data" / "tweets_to_post.json"
HISTORY_PATH = ROOT / "data" / "posted_news.json"

# Align with validate.py hard limits
MAX_TWEET_LEN = 240
MIN_TWEET_LEN = 70
MAX_HISTORY_ENTRIES = 1200
# Minimum hours between live posts (env MIN_POST_GAP_HOURS overrides)
DEFAULT_MIN_POST_GAP_HOURS = 2.0
# Live schedule starts this UTC date (YYYY-MM-DD); env LIVE_START_DATE overrides
DEFAULT_LIVE_START_DATE = "2026-07-12"

_URL_RE = re.compile(
    r"(https?://|www\.)|"
    r"\b[\w-]+\.(com|ai|io|org|net|co|dev|app|news|tech)(?:/[\w./?%&=-]*)?\b",
    re.I,
)
_ELLIPSIS_RE = re.compile(r"…|\.\.\.")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def get_x_client() -> tweepy.Client:
    # wait_on_rate_limit=False: fail fast rather than hang until GHA timeout
    return tweepy.Client(
        consumer_key=require_env("X_API_KEY"),
        consumer_secret=require_env("X_API_SECRET"),
        access_token=require_env("X_ACCESS_TOKEN"),
        access_token_secret=require_env("X_ACCESS_TOKEN_SECRET"),
        wait_on_rate_limit=False,
    )


def load_tweets(path: Path = TWEETS_PATH) -> list[dict[str, Any]]:
    data = load_json_safe(path, {"tweets": []})
    if not isinstance(data, dict):
        return []
    tweets = data.get("tweets") or []
    return tweets if isinstance(tweets, list) else []


def save_history(history: dict[str, Any], path: Path = HISTORY_PATH) -> None:
    posted = history.get("posted") or []
    if len(posted) > MAX_HISTORY_ENTRIES:
        history["posted"] = posted[-MAX_HISTORY_ENTRIES:]
    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(path, history)
    logger.info("History saved (%d entries) → %s", len(history["posted"]), path)


def final_quality_check(tweet: str) -> tuple[bool, str]:
    text = (tweet or "").strip()
    if not text:
        return False, "empty"
    if len(text) > MAX_TWEET_LEN:
        return False, f"length {len(text)} > {MAX_TWEET_LEN}"
    if len(text) < MIN_TWEET_LEN:
        return False, "too short"
    if "_" in text:
        return False, "underscore"
    if "—" in text or "–" in text:
        return False, "em/en dash"
    if _ELLIPSIS_RE.search(text):
        return False, "ellipsis"
    if _URL_RE.search(text):
        return False, "url"
    if re.search(r"(?<!\w)#\w+", text):
        return False, "hashtag"
    hard = hard_block_violations(text)
    if hard:
        return False, hard[0]
    return True, "ok"


def live_start_date() -> str:
    return (os.environ.get("LIVE_START_DATE") or DEFAULT_LIVE_START_DATE).strip()


def min_post_gap_hours() -> float:
    raw = os.environ.get("MIN_POST_GAP_HOURS") or str(DEFAULT_MIN_POST_GAP_HOURS)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_MIN_POST_GAP_HOURS


def before_live_start(now: datetime | None = None) -> bool:
    """True if we should not live-post yet (before LIVE_START_DATE UTC)."""
    now = now or datetime.now(timezone.utc)
    try:
        start = datetime.strptime(live_start_date(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Invalid LIVE_START_DATE %r — allowing posts", live_start_date())
        return False
    return now < start


def hours_since_last_post(history: dict[str, Any]) -> float | None:
    """Hours since most recent successful post; None if no history."""
    latest: datetime | None = None
    for item in history.get("posted") or []:
        if not isinstance(item, dict):
            continue
        ts = item.get("posted_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if latest is None or dt > latest:
            latest = dt
    if latest is None:
        return None
    return (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0


def already_posted(history: dict[str, Any], url: str, tweet: str) -> bool:
    norm = normalize_url(url) if url else ""
    for item in history.get("posted", []):
        if not isinstance(item, dict):
            continue
        if norm and normalize_url(item.get("url") or "") == norm:
            return True
        if tweet and item.get("tweet") == tweet:
            return True
    return False


def post_tweet(client: tweepy.Client, text: str) -> str | None:
    try:
        response = client.create_tweet(text=text)
    except tweepy.TooManyRequests as exc:
        logger.error("X rate limited: %s", exc)
        return None
    except tweepy.TweepyException as exc:
        logger.error("X API error: %s", exc)
        return None
    data = getattr(response, "data", None) or {}
    tweet_id = data.get("id")
    if tweet_id:
        logger.info("Posted tweet id=%s", tweet_id)
    return str(tweet_id) if tweet_id else None


def post_all(
    tweets: list[dict[str, Any]] | None = None,
    *,
    dry_run: bool = False,
    auto_post: bool = False,
) -> tuple[int, int, str]:
    """
    Returns (posted_count, failure_count, skip_reason).
    skip_reason is set for intentional skips (not CI failures).
    """
    if tweets is None:
        tweets = load_tweets()

    tweets = [t for t in tweets if t.get("validation_approved") is True]
    if not tweets:
        logger.info("No validation_approved tweets — nothing to post.")
        return 0, 0, "no_approved"

    if not auto_post and not dry_run:
        logger.warning(
            "AUTO_POST is not enabled. Refusing to post. "
            "Set AUTO_POST=true to publish, or DRY_RUN=true to simulate."
        )
        for t in tweets:
            logger.info("WOULD POST (%d): %s", len(t.get("tweet") or ""), t.get("tweet"))
        return 0, 0, "auto_post_disabled"

    # Schedule goes live only from LIVE_START_DATE (default tomorrow 2026-07-12 UTC)
    if not dry_run and before_live_start():
        logger.info(
            "Before LIVE_START_DATE=%s UTC — skipping live post (starts tomorrow).",
            live_start_date(),
        )
        for t in tweets:
            logger.info("HELD UNTIL START (%d): %s", len(t.get("tweet") or ""), t.get("tweet"))
        return 0, 0, "before_live_start"

    history = load_history_strict(HISTORY_PATH)

    # Enforce ≥2h gap between live posts
    gap_h = min_post_gap_hours()
    if not dry_run and gap_h > 0:
        since = hours_since_last_post(history)
        if since is not None and since < gap_h:
            logger.info(
                "Last post was %.2fh ago (< %.1fh gap) — skipping to keep spacing.",
                since,
                gap_h,
            )
            return 0, 0, "min_gap"

    posted_count = 0
    failure_count = 0
    client: tweepy.Client | None = None
    if not dry_run:
        client = get_x_client()

    for i, item in enumerate(tweets, start=1):
        tweet = (item.get("tweet") or "").strip()
        url = normalize_url(item.get("url") or "")
        title = (item.get("title") or "").strip()
        source = (item.get("source") or "").strip()

        logger.info("--- Tweet 1 live max (gap enforced); item %d/%d ---", i, len(tweets))
        logger.info("Text (%d): %s", len(tweet), tweet)

        ok, reason = final_quality_check(tweet)
        if not ok:
            logger.warning("Skipped (final gate): %s", reason)
            failure_count += 1
            continue
        if already_posted(history, url, tweet):
            logger.warning("Skipped (already posted)")
            continue

        if dry_run:
            logger.info("[DRY RUN] Would post.")
            tweet_id = f"dry-run-{int(time.time())}"
        else:
            assert client is not None
            tweet_id = post_tweet(client, tweet)
            if not tweet_id:
                failure_count += 1
                continue

        history.setdefault("posted", []).append(
            {
                "url": url,
                "title": title,
                "source": source,
                "tweet": tweet,
                "tweet_id": tweet_id,
                "validation": item.get("validation"),
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        posted_count += 1

        if not dry_run:
            save_history(history)

        # At most one live post per run (gap is between runs)
        break

    if dry_run and posted_count:
        logger.info("Dry run complete (%d). History not written.", posted_count)
    elif not posted_count:
        logger.info("No new posts.")

    return posted_count, failure_count, ""


def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    auto_post = os.environ.get("AUTO_POST", "").lower() in {"1", "true", "yes"}
    if dry_run:
        logger.info("DRY_RUN enabled.")
    if auto_post:
        logger.info("AUTO_POST enabled.")

    try:
        count, failures, skip = post_all(dry_run=dry_run, auto_post=auto_post)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except EnvironmentError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Done. Posted %d tweet(s), failures=%d, skip=%s.", count, failures, skip or "none")

    # Intentional skips are success (green CI)
    if skip in {"before_live_start", "min_gap", "no_approved", "auto_post_disabled"}:
        return 0

    # Live mode: hard fail only when we tried and failed
    if auto_post and not dry_run and failures and count == 0:
        logger.error("Live post failed for approved tweets.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
