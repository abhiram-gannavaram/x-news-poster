#!/usr/bin/env python3
"""
Fetch latest AI/Tech news from free RSS feeds.

Loads history from data/posted_news.json, filters already-posted items,
and writes fresh candidates to data/candidates.json.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import feedparser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_news")

# Project root (parent of agents/)
ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "posted_news.json"
CANDIDATES_PATH = ROOT / "data" / "candidates.json"

# ---------------------------------------------------------------------------
# Free AI / Tech RSS feeds (8–10 solid sources)
# ---------------------------------------------------------------------------
RSS_FEEDS: list[dict[str, str]] = [
    {
        "name": "Hacker News",
        "url": "https://hnrss.org/frontpage",
        "category": "tech",
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "category": "tech",
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "category": "tech",
    },
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
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog/rss.xml",
        "category": "ai",
    },
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

# Keywords that strongly signal AI / ML relevance (used for soft ranking)
AI_KEYWORDS = re.compile(
    r"\b("
    r"ai|artificial intelligence|machine learning|deep learning|llm|gpt|"
    r"claude|gemini|openai|anthropic|neural|transformer|diffusion|"
    r"generative|chatbot|agent|rag|embedding|mlops|computer vision|"
    r"nlp|foundation model|multimodal|reasoning|autonomous"
    r")\b",
    re.IGNORECASE,
)

# Max items kept per feed and overall after fetch
MAX_PER_FEED = 8
MAX_CANDIDATES = 40


def normalize_url(url: str) -> str:
    """Strip tracking params and fragments for stable dedup keys."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    # Drop common trackers; keep path + netloc
    clean = urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", "")
    )
    return clean


def load_history(path: Path = HISTORY_PATH) -> dict[str, Any]:
    """Load posted-news history; return empty structure if missing/corrupt."""
    if not path.exists():
        return {"posted": [], "last_updated": None}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if "posted" not in data or not isinstance(data["posted"], list):
            data["posted"] = []
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read history %s: %s — starting fresh", path, exc)
        return {"posted": [], "last_updated": None}


def posted_url_set(history: dict[str, Any]) -> set[str]:
    """Build a set of normalized URLs already posted."""
    urls: set[str] = set()
    for item in history.get("posted", []):
        if isinstance(item, dict) and item.get("url"):
            urls.add(normalize_url(item["url"]))
        elif isinstance(item, str):
            urls.add(normalize_url(item))
    return urls


def strip_html(text: str) -> str:
    """Remove simple HTML tags from feed summaries."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def entry_published(entry: Any) -> str | None:
    """Best-effort ISO timestamp from a feed entry."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue
    return None


def score_item(title: str, summary: str, category: str) -> float:
    """
    Lightweight relevance score so AI-heavy items rank higher before Claude.

    Pure heuristic — Claude still does the final pick.
    """
    text = f"{title} {summary}"
    matches = len(AI_KEYWORDS.findall(text))
    base = 1.0 if category == "ai" else 0.5
    return base + min(matches * 0.4, 3.0)


def fetch_feed(feed: dict[str, str]) -> list[dict[str, Any]]:
    """Parse one RSS feed and return normalized article dicts."""
    name, url, category = feed["name"], feed["url"], feed["category"]
    logger.info("Fetching %s …", name)

    try:
        # User-Agent helps some feeds (e.g. Reddit) avoid 403s
        parsed = feedparser.parse(
            url,
            request_headers={
                "User-Agent": "x-news-poster/1.0 (+https://github.com/x-news-poster)"
            },
        )
    except Exception as exc:  # noqa: BLE001 — keep other feeds running
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

        summary = strip_html(
            entry.get("summary") or entry.get("description") or ""
        )[:500]

        items.append(
            {
                "title": title,
                "url": link,
                "summary": summary,
                "source": name,
                "category": category,
                "published": entry_published(entry),
                "relevance_score": score_item(title, summary, category),
            }
        )

    logger.info("  → %d items from %s", len(items), name)
    return items


def fetch_all_news(
    feeds: list[dict[str, str]] | None = None,
    history_path: Path = HISTORY_PATH,
) -> list[dict[str, Any]]:
    """
    Fetch all feeds, drop already-posted URLs, sort by relevance.

    Returns up to MAX_CANDIDATES unique stories.
    """
    feeds = feeds or RSS_FEEDS
    history = load_history(history_path)
    seen_posted = posted_url_set(history)
    logger.info("History has %d previously posted URL(s)", len(seen_posted))

    raw: list[dict[str, Any]] = []
    for feed in feeds:
        raw.extend(fetch_feed(feed))

    # Dedup within this run + filter history
    seen_urls: set[str] = set()
    fresh: list[dict[str, Any]] = []
    for item in raw:
        url = item["url"]
        if url in seen_urls or url in seen_posted:
            continue
        seen_urls.add(url)
        fresh.append(item)

    fresh.sort(key=lambda x: x["relevance_score"], reverse=True)
    candidates = fresh[:MAX_CANDIDATES]
    logger.info(
        "Collected %d raw → %d unique fresh → keeping top %d",
        len(raw),
        len(fresh),
        len(candidates),
    )
    return candidates


def save_candidates(
    candidates: list[dict[str, Any]], path: Path = CANDIDATES_PATH
) -> None:
    """Write candidates for the analyze step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(candidates),
        "candidates": candidates,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %d candidates → %s", len(candidates), path)


def main() -> int:
    candidates = fetch_all_news()
    if not candidates:
        logger.warning("No fresh candidates found. Exiting successfully (nothing to do).")
        save_candidates([])
        return 0
    save_candidates(candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
