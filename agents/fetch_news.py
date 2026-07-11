#!/usr/bin/env python3
"""
Fetch latest AI/Tech news from free RSS feeds.

Loads history from data/posted_news.json, filters already-posted items,
and writes fresh candidates to data/candidates.json.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.utils import (  # noqa: E402
    atomic_write_json,
    load_history_strict,
    normalize_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_news")

HISTORY_PATH = ROOT / "data" / "posted_news.json"
CANDIDATES_PATH = ROOT / "data" / "candidates.json"

RSS_FEEDS: list[dict[str, str]] = [
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "category": "tech"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "category": "tech"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "category": "tech"},
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "category": "tech",
    },
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
        "category": "ai",
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/feed/",
        "category": "ai",
    },
    {
        "name": "Reddit r/MachineLearning",
        "url": "https://www.reddit.com/r/MachineLearning/.rss",
        "category": "ai",
    },
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "category": "ai"},
    {
        "name": "Google AI Blog",
        "url": "https://blog.google/technology/ai/rss/",
        "category": "ai",
    },
    {
        "name": "Wired AI",
        "url": "https://www.wired.com/feed/tag/ai/latest/rss",
        "category": "ai",
    },
]

AI_KEYWORDS = re.compile(
    r"\b("
    r"ai|artificial intelligence|machine learning|deep learning|llm|gpt|"
    r"claude|gemini|openai|anthropic|neural|transformer|diffusion|"
    r"generative|chatbot|agent|rag|embedding|mlops|computer vision|"
    r"nlp|foundation model|multimodal|reasoning|autonomous"
    r")\b",
    re.IGNORECASE,
)

MAX_PER_FEED = 8
MAX_CANDIDATES = 30
MAX_AGE_HOURS = 48.0
# Undated items are capped (cannot prove freshness)
MAX_UNDATED = 5


def posted_url_set(history: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for item in history.get("posted", []):
        if isinstance(item, dict) and item.get("url"):
            urls.add(normalize_url(item["url"]))
        elif isinstance(item, str):
            urls.add(normalize_url(item))
    return urls


def strip_html(text: str) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def entry_published(entry: Any) -> str | None:
    """
    Best-effort ISO timestamp.

    feedparser times are treated as UTC when TZ is unknown (RSS limitation).
    """
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue
    return None


def age_hours_from_iso(published: str | None) -> float | None:
    if not published:
        return None
    try:
        dt = datetime.fromisoformat(published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return None


def score_item(
    title: str,
    summary: str,
    category: str,
    age_hours: float | None = None,
) -> float:
    text = f"{title} {summary}"
    matches = len(AI_KEYWORDS.findall(text))
    base = 1.0 if category == "ai" else 0.5
    score = base + min(matches * 0.4, 3.0)

    if age_hours is not None:
        if age_hours <= 12:
            score += 3.0
        elif age_hours <= 24:
            score += 2.0
        elif age_hours <= 48:
            score += 0.5
        else:
            score -= 2.0
    else:
        # Undated: mild penalty vs known-fresh
        score -= 1.0
    return score


def fetch_feed(feed: dict[str, str]) -> list[dict[str, Any]]:
    name, url, category = feed["name"], feed["url"], feed["category"]
    logger.info("Fetching %s …", name)

    try:
        parsed = feedparser.parse(
            url,
            request_headers={
                "User-Agent": "x-news-poster/1.0 (+https://github.com/x-news-poster)"
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch %s: %s", name, exc)
        return []

    if getattr(parsed, "bozo", False) and not parsed.entries:
        logger.warning(
            "Feed parse issue for %s: %s",
            name,
            getattr(parsed, "bozo_exception", "unknown"),
        )
        return []

    items: list[dict[str, Any]] = []
    for entry in parsed.entries[:MAX_PER_FEED]:
        title = (entry.get("title") or "").strip()
        link = normalize_url(entry.get("link") or entry.get("id") or "")
        if not title or not link:
            continue

        summary = strip_html(entry.get("summary") or entry.get("description") or "")[:500]
        published = entry_published(entry)
        age_hours = age_hours_from_iso(published)

        items.append(
            {
                "title": title,
                "url": link,
                "summary": summary,
                "source": name,
                "category": category,
                "published": published,
                "age_hours": age_hours,
                "relevance_score": score_item(title, summary, category, age_hours),
            }
        )

    logger.info("  → %d items from %s", len(items), name)
    return items


def fetch_all_news(
    feeds: list[dict[str, str]] | None = None,
    history_path: Path = HISTORY_PATH,
) -> list[dict[str, Any]]:
    feeds = feeds or RSS_FEEDS
    history = load_history_strict(history_path)
    seen_posted = posted_url_set(history)
    logger.info("History has %d previously posted URL(s)", len(seen_posted))

    raw: list[dict[str, Any]] = []
    for feed in feeds:
        raw.extend(fetch_feed(feed))

    seen_urls: set[str] = set()
    dated: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    dropped_stale = 0

    for item in raw:
        url = item["url"]
        if url in seen_urls or url in seen_posted:
            continue
        age = item.get("age_hours")
        if age is not None and age > MAX_AGE_HOURS:
            dropped_stale += 1
            continue
        seen_urls.add(url)
        if age is None:
            undated.append(item)
        else:
            dated.append(item)

    undated = undated[:MAX_UNDATED]
    fresh = dated + undated

    fresh.sort(
        key=lambda x: (
            x.get("age_hours") is None,
            x.get("age_hours") if x.get("age_hours") is not None else 9999,
            -(x.get("relevance_score") or 0),
        )
    )
    candidates = fresh[:MAX_CANDIDATES]
    logger.info(
        "Collected %d raw → %d unique (dropped %d stale >%sh, undated kept %d) → top %d",
        len(raw),
        len(fresh),
        dropped_stale,
        int(MAX_AGE_HOURS),
        len(undated),
        len(candidates),
    )
    return candidates


def save_candidates(candidates: list[dict[str, Any]], path: Path = CANDIDATES_PATH) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(candidates),
        "candidates": candidates,
    }
    atomic_write_json(path, payload)
    logger.info("Wrote %d candidates → %s", len(candidates), path)


def main() -> int:
    try:
        candidates = fetch_all_news()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    if not candidates:
        logger.warning("No fresh candidates found.")
        save_candidates([])
        return 0
    save_candidates(candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
