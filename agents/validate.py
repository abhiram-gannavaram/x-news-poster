#!/usr/bin/env python3
"""
Multi-layer validation before anything can be posted.

Layers:
1. Deterministic style gate
2. Fact grounding vs verified research facts
3. Quality score
4. Optional single rewrite, then re-check all gates
5. Mark validation_approved only if ALL pass

Writes:
- data/tweets_to_post.json (approved + rejected drafts for debugging)
- data/validation_report.json
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.bedrock_client import extract_json_safe, invoke_claude  # noqa: E402
from agents.content_safety import (  # noqa: E402
    hard_block_violations,
    parse_safety_result,
    safety_check_prompt,
)
from agents.utils import atomic_write_json, load_json_safe, safe_float  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("validate")

DRAFTS_PATH = ROOT / "data" / "tweets_to_post.json"
REPORT_PATH = ROOT / "data" / "validation_report.json"

MAX_TWEET_LEN = 240
MIN_TWEET_LEN = 70
MIN_QUALITY_SCORE = 7.0
MIN_SUBSTANCE_SCORE = 6.0

AI_TELLS = (
    "delve",
    "landscape",
    "leverage",
    "robust",
    "unlock",
    "game-changer",
    "game changer",
    "navigate",
    "tapestry",
    "crucial",
    "seamless",
    "cutting-edge",
    "cutting edge",
    "in today's world",
    "it's worth noting",
    "it is worth noting",
    "furthermore",
    "moreover",
    "holistic",
    "synergy",
    "paradigm",
    "revolutionize",
    "revolutionary",
    "excited to share",
    "proud to announce",
    "as an ai",
    "as a language model",
    "in conclusion",
    "multi-faceted",
    "multifaceted",
    "underscore",
    "shed light",
    "at the end of the day",
)

PR_TELLS = (
    "just launched",
    "just announced",
    "check this out",
    "must read",
    "thread:",
    "link in bio",
    "game changing",
)

# Bare domains + full URLs (word-ish boundaries)
_URL_RE = re.compile(
    r"(https?://|www\.)|"
    r"\b[\w-]+\.(com|ai|io|org|net|co|dev|app|news|tech)(?:/[\w./?%&=-]*)?\b",
    re.I,
)
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F]"
)
_ELLIPSIS_RE = re.compile(r"…|\.\.\.")


def _phrase_in_text(phrase: str, text: str) -> bool:
    """Word-boundary-ish match to avoid 'breaking' in 'groundbreaking'."""
    if " " in phrase or "-" in phrase:
        return phrase in text
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def style_gate(tweet: str, url: str = "") -> tuple[bool, list[str]]:
    violations: list[str] = []
    text = (tweet or "").strip()

    if not text:
        return False, ["empty"]
    if len(text) > MAX_TWEET_LEN:
        violations.append(f"too long ({len(text)} > {MAX_TWEET_LEN})")
    if len(text) < MIN_TWEET_LEN:
        violations.append(f"too short ({len(text)} < {MIN_TWEET_LEN})")
    if "_" in text:
        violations.append("contains underscore _")
    if "—" in text or "–" in text:
        violations.append("contains em/en dash")
    if _ELLIPSIS_RE.search(text):
        violations.append("ellipsis / truncated")
    if text.endswith((",", ";", ":", "-")):
        violations.append("ends mid-thought")
    if _URL_RE.search(text):
        violations.append("contains URL/domain")
    if url:
        domain = urlparse(url).netloc.replace("www.", "")
        if domain and domain.lower() in text.lower():
            violations.append("contains source domain")
    if _HASHTAG_RE.search(text):
        violations.append("hashtag")
    if _EMOJI_RE.search(text):
        violations.append("emoji")

    low = text.lower()
    for phrase in AI_TELLS:
        if _phrase_in_text(phrase, low):
            violations.append(f"AI voice: '{phrase}'")
            break
    for phrase in PR_TELLS:
        if _phrase_in_text(phrase, low):
            violations.append(f"PR voice: '{phrase}'")
            break
    # "breaking" as standalone PR word, not "groundbreaking"
    if re.search(r"(?<![a-z])breaking(?![a-z])", low):
        violations.append("PR voice: 'breaking'")

    if re.match(
        r"(?i)^(anthropic|openai|google|meta|microsoft|amazon|nvidia)\s+"
        r"(just|launched|announced|unveiled)\b",
        text,
    ):
        violations.append("news-bot lead")

    # Hard safety blocklist (racism / extreme harm phrases)
    violations.extend(hard_block_violations(text))

    return (len(violations) == 0), violations


def content_safety_check(tweet: str) -> dict[str, Any]:
    """Claude safety reviewer — fail closed."""
    raw = invoke_claude(safety_check_prompt(tweet), max_tokens=400, temperature=0.0)
    return extract_json_safe(
        raw,
        {
            "safe": False,
            "categories": ["safety_parse_failure"],
            "feedback": "safety JSON parse failure",
        },
    )


def is_grounded(fact: dict[str, Any], facts: list[Any]) -> bool:
    if not facts:
        return False
    if not bool(fact.get("grounded")):
        return False
    risk = (fact.get("risk_level") or "high").lower()
    # Missing risk_level defaults high (fail closed)
    return risk in {"low", "medium"}


def quality_ok(quality: dict[str, Any]) -> bool:
    q_score = safe_float(quality.get("score"), 0.0)
    substance = safe_float(quality.get("substance"), 0.0)
    human = safe_float(quality.get("human"), 0.0)
    return (
        bool(quality.get("approved"))
        and q_score >= MIN_QUALITY_SCORE
        and substance >= MIN_SUBSTANCE_SCORE
        and human >= 6
    )


def fact_check(tweet: str, facts: list[str], title: str, angle: str) -> dict[str, Any]:
    facts_txt = "\n".join(f"- {f}" for f in facts) if facts else "(no verified facts)"
    prompt = f"""You are a strict fact checker for short social posts about tech/AI.

POST:
\"\"\"{tweet}\"\"\"

SOURCE TITLE: {title}
BUILDER ANGLE (hint only): {angle}
VERIFIED FACTS FROM RESEARCH:
{facts_txt}

Check:
1. Does the post invent specific claims, numbers, or motives not supported by verified facts?
2. Is the take a reasonable inference, or is it stated as hard fact without support?
3. Would a careful engineer be embarrassed if the source is wrong / thin?

Respond ONLY JSON:
{{
  "grounded": true/false,
  "invented_claims": ["list unsupported claims, empty if none"],
  "risk_level": "low"|"medium"|"high",
  "feedback": "what to fix if not grounded, else empty",
  "score": 1-10
}}

Rules:
- ALWAYS include risk_level
- grounded=false if risk_level is high OR any material invented claim
- Opinions are OK if clearly framed as takes, not fake facts
- If verified facts are empty, grounded=false and risk_level=high
"""
    raw = invoke_claude(prompt, max_tokens=700, temperature=0.1)
    result = extract_json_safe(
        raw,
        {
            "grounded": False,
            "invented_claims": ["fact-check parse failure"],
            "risk_level": "high",
            "feedback": "fact-check JSON parse failure",
            "score": 0,
        },
    )
    if not result.get("risk_level"):
        result["risk_level"] = "high"
    return result


def quality_check(tweet: str) -> dict[str, Any]:
    prompt = f"""You are a quality reviewer for X posts by a real engineer (not a news bot).

Post:
\"\"\"{tweet}\"\"\"

Score each dimension 1-10 (be strict; most posts are 5-7):
1. HOOK: first line stops the scroll
2. SUBSTANCE: original insight, not bland recap
3. HUMAN: sounds like a person, not AI corporate voice
4. COMPLETE: finished thought, no truncation
5. DM_WORTHY: worth forwarding to a coworker
6. SPAM_SAFE: no bait, no hashtags spam, no bot patterns

Approve only if overall >= {MIN_QUALITY_SCORE} AND substance >= {MIN_SUBSTANCE_SCORE} AND human >= 6.

Respond ONLY JSON:
{{
  "hook": 1-10,
  "substance": 1-10,
  "human": 1-10,
  "complete": 1-10,
  "dm_worthy": 1-10,
  "spam_safe": 1-10,
  "score": <overall 1-10>,
  "approved": true/false,
  "feedback": "what failed, empty if approved",
  "improved_post": "rewrite under 220 chars fixing issues IF approved is false; else copy original exactly. No links, no hashtags, no underscores, no em dashes, no ellipsis."
}}
"""
    raw = invoke_claude(prompt, max_tokens=900, temperature=0.2)
    return extract_json_safe(
        raw,
        {
            "score": 0,
            "substance": 0,
            "human": 0,
            "approved": False,
            "feedback": "quality JSON parse failure",
            "improved_post": tweet,
        },
    )


def validate_one(draft: dict[str, Any]) -> dict[str, Any]:
    tweet = (draft.get("tweet") or "").strip()
    url = (draft.get("url") or "").strip()
    facts = draft.get("verified_facts") or []
    if not isinstance(facts, list):
        facts = []
    title = draft.get("title") or ""
    angle = draft.get("builder_angle") or ""

    report: dict[str, Any] = {
        "title": title,
        "url": url,
        "original_tweet": tweet,
        "layers": {},
        "approved": False,
        "final_tweet": "",
        "reject_reasons": [],
        "used_rewrite": False,
    }

    ok, violations = style_gate(tweet, url)
    report["layers"]["style"] = {"passed": ok, "violations": violations}
    if not ok:
        report["reject_reasons"].extend(violations)
        logger.warning("Style gate failed: %s", violations)

    fact = fact_check(tweet, facts, title, angle)
    grounded = is_grounded(fact, facts)
    if not facts:
        fact["feedback"] = (fact.get("feedback") or "") + " | no verified facts"
    report["layers"]["fact"] = fact
    if not grounded:
        report["reject_reasons"].append(
            f"fact: {fact.get('feedback') or fact.get('invented_claims') or 'not grounded'}"
        )

    quality = quality_check(tweet)
    q_ok = quality_ok(quality)
    report["layers"]["quality"] = quality
    if not q_ok:
        report["reject_reasons"].append(
            f"quality score={safe_float(quality.get('score'))} "
            f"substance={safe_float(quality.get('substance'))} "
            f"human={safe_float(quality.get('human'))}: {quality.get('feedback')}"
        )

    # Content safety (no racism / hate / harmful / toxic attacks)
    safety = content_safety_check(tweet)
    safe_ok, safe_reason = parse_safety_result(safety)
    report["layers"]["safety"] = safety
    if not safe_ok:
        report["reject_reasons"].append(f"safety: {safe_reason}")

    candidate = tweet
    fact_final = fact
    quality_final = quality
    safety_final = safety

    if not ok or not grounded or not q_ok or not safe_ok:
        rewrite_seed = (quality.get("improved_post") or tweet).strip()
        rewrite_prompt = f"""Rewrite this X post so it passes every rule. Keep the same meaning and stay inside the verified facts.

ORIGINAL:
\"\"\"{tweet}\"\"\"

VERIFIED FACTS:
{chr(10).join('- ' + str(f) for f in facts) if facts else '(none)'}

FAILURES TO FIX:
{json.dumps(report.get('reject_reasons') or [], ensure_ascii=False)}
Quality feedback: {quality.get('feedback') or ''}
Fact feedback: {fact.get('feedback') or ''}

Rules for rewrite:
- 70 to 220 characters (hard max 240)
- No links, hashtags, emojis, underscores _, em dashes, ellipsis
- Human engineer voice, no corporate AI sludge
- Complete sentences
- No invented claims
- No racism, hate, harassment, or harmful content; keep constructive

Return ONLY JSON: {{"tweet": "<rewritten post>"}}
"""
        raw_rw = invoke_claude(rewrite_prompt, max_tokens=400, temperature=0.35)
        improved = (
            extract_json_safe(raw_rw, {"tweet": rewrite_seed}).get("tweet") or rewrite_seed
        ).strip()
        logger.info("Trying rewrite (%d chars): %s", len(improved), improved[:120])
        ok2, viol2 = style_gate(improved, url)
        fact2 = fact_check(improved, facts, title, angle)
        grounded2 = is_grounded(fact2, facts)
        quality2 = quality_check(improved)
        q2 = quality_ok(quality2)
        safety2 = content_safety_check(improved)
        safe2, safe2_reason = parse_safety_result(safety2)
        report["layers"]["rewrite"] = {
            "tweet": improved,
            "style_passed": ok2,
            "style_violations": viol2,
            "fact": fact2,
            "quality": quality2,
            "safety": safety2,
        }
        if ok2 and grounded2 and q2 and safe2:
            candidate = improved
            ok, grounded, q_ok, safe_ok = True, True, True, True
            fact_final, quality_final, safety_final = fact2, quality2, safety2
            report["used_rewrite"] = True
            report["reject_reasons"] = []
            logger.info("Rewrite passed all gates")
        else:
            reasons = []
            if not ok2:
                reasons.extend(viol2)
            if not grounded2:
                reasons.append(f"fact: {fact2.get('feedback')}")
            if not q2:
                reasons.append(f"quality: {quality2.get('feedback')}")
            if not safe2:
                reasons.append(f"safety: {safe2_reason}")
            report["reject_reasons"] = reasons
            logger.info("Rewrite still failed: %s", reasons)

    if ok and grounded and q_ok and safe_ok:
        final_ok, final_viol = style_gate(candidate, url)
        # Re-check safety on final text
        if final_ok:
            hard = hard_block_violations(candidate)
            if hard:
                final_ok = False
                final_viol = hard
        if final_ok:
            report["approved"] = True
            report["final_tweet"] = candidate
            report["reject_reasons"] = []
            report["final_scores"] = {
                "quality_score": safe_float(quality_final.get("score")),
                "substance": safe_float(quality_final.get("substance")),
                "human": safe_float(quality_final.get("human")),
                "fact_score": safe_float(fact_final.get("score")),
                "risk_level": fact_final.get("risk_level") or "high",
                "safety_safe": bool(safety_final.get("safe")),
            }
        else:
            report["reject_reasons"] = final_viol
    else:
        report["approved"] = False
        report["final_tweet"] = ""

    return report


def load_drafts(path: Path = DRAFTS_PATH) -> list[dict[str, Any]]:
    data = load_json_safe(path, {"tweets": []})
    if not isinstance(data, dict):
        return []
    tweets = data.get("tweets") or []
    return tweets if isinstance(tweets, list) else []


def save_outputs(
    queue: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    approved_count: int,
) -> None:
    """
    Persist full queue: approved items first, then rejected (for debugging).
    post_to_x only uses validation_approved=true.
    """
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": approved_count,
        "tweets": queue,
        "note": "Only validation_approved=true items may be posted. Rejected kept for debug.",
    }
    atomic_write_json(DRAFTS_PATH, payload)

    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "approved_count": approved_count,
        "reports": reports,
    }
    atomic_write_json(REPORT_PATH, report)
    logger.info(
        "Validation done: %d approved → %s | report → %s",
        approved_count,
        DRAFTS_PATH,
        REPORT_PATH,
    )


def run_validation(drafts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if drafts is None:
        drafts = load_drafts()
    # Only validate unapproved drafts (or all with status draft)
    if not drafts:
        logger.warning("No drafts to validate.")
        save_outputs([], [], 0)
        return []

    reports: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for draft in drafts:
        # Skip already-approved leftovers
        if draft.get("validation_approved") is True and draft.get("status") == "approved":
            approved.append(draft)
            continue

        logger.info("Validating: %s", (draft.get("tweet") or "")[:100])
        rep = validate_one(draft)
        reports.append(rep)
        if rep.get("approved") and rep.get("final_tweet"):
            out = dict(draft)
            out["tweet"] = rep["final_tweet"]
            out["char_count"] = len(rep["final_tweet"])
            out["validation_approved"] = True
            out["status"] = "approved"
            scores = rep.get("final_scores") or {}
            out["validation"] = {
                "quality_score": scores.get("quality_score"),
                "substance": scores.get("substance"),
                "human": scores.get("human"),
                "fact_score": scores.get("fact_score"),
                "risk_level": scores.get("risk_level"),
                "used_rewrite": rep.get("used_rewrite", False),
            }
            approved.append(out)
            logger.info("APPROVED (%d chars): %s", out["char_count"], out["tweet"])
        else:
            out = dict(draft)
            out["validation_approved"] = False
            out["status"] = "rejected"
            out["reject_reasons"] = rep.get("reject_reasons") or []
            out["original_tweet"] = rep.get("original_tweet")
            rejected.append(out)
            logger.warning("REJECTED: %s", rep.get("reject_reasons"))

    # Approved only in the postable prefix for clarity; rejected retained
    queue = approved + rejected
    save_outputs(queue, reports, approved_count=len(approved))
    return approved


def main() -> int:
    approved = run_validation()
    if not approved:
        logger.warning("Nothing approved — will not post.")
        # Not a hard failure: empty news day is OK
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
