#!/usr/bin/env python3
"""
Post approved tweets to X (Twitter) via API v2 (tweepy) and update history.

Reads data/tweets_to_post.json, posts each tweet, and appends to
data/posted_news.json so future runs skip the same stories.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tweepy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("post_to_x")

ROOT = Path(__file__).resolve().parent.parent
TWEETS_PATH = ROOT / "data" / "tweets_to_post.json"
HISTORY_PATH = ROOT / "data" / "posted_news.json"

MAX_TWEET_LEN = 280
# Keep history from growing forever (roughly ~90 days at 6/day * 2 tweets)
MAX_HISTORY_ENTRIES = 1200


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            "Set it in GitHub Actions secrets / local env."
        )
    return value


def get_x_client() -> tweepy.Client:
    """
    Build a tweepy Client for X API v2 user-context writes.

    Needs OAuth 1.0a user credentials (API key/secret + access token/secret).
    """
    return tweepy.Client(
        consumer_key=require_env("X_API_KEY"),
        consumer_secret=require_env("X_API_SECRET"),
        access_token=require_env("X_ACCESS_TOKEN"),
        access_token_secret=require_env("X_ACCESS_TOKEN_SECRET"),
        wait_on_rate_limit=True,
    )


def load_tweets(path: Path = TWEETS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        logger.warning("No tweets file at %s", path)
        return []
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tweets") or []


def load_history(path: Path = HISTORY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"posted": [], "last_updated": None}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("posted"), list):
            data["posted"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"posted": [], "last_updated": None}


def save_history(history: dict[str, Any], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Cap size (oldest first in list → keep newest tail)
    posted = history.get("posted") or []
    if len(posted) > MAX_HISTORY_ENTRIES:
        history["posted"] = posted[-MAX_HISTORY_ENTRIES:]
    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    with path.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    logger.info("History saved (%d entries) → %s", len(history["posted"]), path)


def final_quality_check(tweet: str) -> tuple[bool, str]:
    """Last-chance validation right before calling X API."""
    import re

    text = (tweet or "").strip()
    if not text:
        return False, "empty"
    if len(text) > MAX_TWEET_LEN:
        return False, f"length {len(text)} > {MAX_TWEET_LEN}"
    if len(text) < 40:
        return False, "too short"
    # Never post half-baked truncated lines or link spam
    if text.endswith(("…", "...")) or "…" in text[-4:]:
        return False, "ellipsis / truncated"
    if re.search(r"https?://|www\.", text, re.I):
        return False, "url not allowed"
    if re.search(r"(?<!\w)#\w+", text):
        return False, "hashtag not allowed"
    return True, "ok"


def already_posted(history: dict[str, Any], url: str) -> bool:
    for item in history.get("posted", []):
        if isinstance(item, dict) and item.get("url") == url:
            return True
        if isinstance(item, str) and item == url:
            return True
    return False


def post_tweet(client: tweepy.Client, text: str) -> str | None:
    """
    Create a tweet. Returns tweet ID on success, None on failure.
    """
    try:
        response = client.create_tweet(text=text)
    except tweepy.Forbidden as exc:
        logger.error("X API Forbidden (check app permissions / elevated access): %s", exc)
        return None
    except tweepy.Unauthorized as exc:
        logger.error("X API Unauthorized (bad credentials): %s", exc)
        return None
    except tweepy.TooManyRequests as exc:
        logger.error("X API rate limited: %s", exc)
        return None
    except tweepy.TweepyException as exc:
        logger.error("X API error: %s", exc)
        return None

    # response.data is typically {"id": "...", "text": "..."}
    data = getattr(response, "data", None) or {}
    tweet_id = data.get("id")
    if tweet_id:
        logger.info("Posted tweet id=%s", tweet_id)
    return str(tweet_id) if tweet_id else None


def post_all(
    tweets: list[dict[str, Any]] | None = None,
    *,
    dry_run: bool = False,
) -> int:
    """
    Post approved tweets and update history.

    Returns number of successfully posted tweets.
    """
    if tweets is None:
        tweets = load_tweets()

    if not tweets:
        logger.info("Nothing to post.")
        return 0

    history = load_history()
    posted_count = 0

    client: tweepy.Client | None = None
    if not dry_run:
        client = get_x_client()

    for i, item in enumerate(tweets, start=1):
        tweet = (item.get("tweet") or "").strip()
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        source = (item.get("source") or "").strip()

        logger.info("--- Tweet %d/%d ---", i, len(tweets))
        logger.info("Title: %s", title)
        logger.info("Text (%d): %s", len(tweet), tweet)

        ok, reason = final_quality_check(tweet)
        if not ok:
            logger.warning("Skipped (quality): %s", reason)
            continue

        if url and already_posted(history, url):
            logger.warning("Skipped (already in history): %s", url)
            continue

        if dry_run:
            logger.info("[DRY RUN] Would post tweet.")
            tweet_id = f"dry-run-{int(time.time())}"
        else:
            assert client is not None
            tweet_id = post_tweet(client, tweet)
            if not tweet_id:
                logger.error("Failed to post; leaving history unchanged for this item.")
                continue
            # Small pause between posts if posting 2
            if i < len(tweets):
                time.sleep(2)

        history.setdefault("posted", []).append(
            {
                "url": url,
                "title": title,
                "source": source,
                "tweet": tweet,
                "tweet_id": tweet_id,
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        posted_count += 1

    if posted_count:
        save_history(history)
    else:
        logger.info("No new posts; history not modified.")

    return posted_count


def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    if dry_run:
        logger.info("DRY_RUN enabled — will not call X API.")

    count = post_all(dry_run=dry_run)
    logger.info("Done. Posted %d tweet(s).", count)
    # Exit 0 even if 0 posts (no news is not a failure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
