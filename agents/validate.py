#!/usr/bin/env python3
"""
Multi-layer validation before anything can be posted.

Layers:
1. Deterministic style gate (length, no URL, no _, no emdash, no AI tells, no ellipsis)
2. Fact grounding check vs verified research facts (Claude)
3. Quality score like the old Lambda quality agent (Claude)
4. Optional single rewrite if close but fixable — then re-run style gate
5. Mark validation_approved only if ALL pass

Never posts. Writes:
- data/tweets_to_post.json (approved or empty)
- data/validation_report.json (full audit trail)
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
    "breaking",
    "check this out",
    "must read",
    "thread:",
    "link in bio",
    "game changing",
)

_URL_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(com|ai|io|org|net|co|dev)/\S*", re.I)
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")


def style_gate(tweet: str, url: str = "") -> tuple[bool, list[str]]:
    """Deterministic hard rejects. Fail closed."""
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
    if "…" in text or text.rstrip().endswith("..."):
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
        if phrase in low:
            violations.append(f"AI voice: '{phrase}'")
            break
    for phrase in PR_TELLS:
        if phrase in low:
            violations.append(f"PR voice: '{phrase}'")
            break

    # News-bot lead patterns
    if re.match(
        r"(?i)^(anthropic|openai|google|meta|microsoft|amazon|nvidia)\s+(just|launched|announced|unveiled)\b",
        text,
    ):
        violations.append("news-bot lead")

    return (len(violations) == 0), violations


def fact_check(tweet: str, facts: list[str], title: str, angle: str) -> dict[str, Any]:
    """Claude: are claims grounded? Any invented detail?"""
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
- grounded=false if risk_level is high OR any material invented claim
- Opinions are OK if clearly framed as takes, not fake facts
- If verified facts are empty, grounded=false
"""
    raw = invoke_claude(prompt, max_tokens=700, temperature=0.1)
    return extract_json_safe(
        raw,
        {
            "grounded": False,
            "invented_claims": ["fact-check parse failure"],
            "risk_level": "high",
            "feedback": "fact-check JSON parse failure",
            "score": 0,
        },
    )


def quality_check(tweet: str) -> dict[str, Any]:
    """Claude quality scorer — ported from the Lambda quality agent."""
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
    }

    # Layer 1: style
    ok, violations = style_gate(tweet, url)
    report["layers"]["style"] = {"passed": ok, "violations": violations}
    if not ok:
        report["reject_reasons"].extend(violations)
        # Try quality improved rewrite path only if style is the only issue and model can fix
        # First still run fact/quality for diagnostics, but prefer rewrite once
        logger.warning("Style gate failed: %s", violations)

    # Layer 2: facts
    fact = fact_check(tweet, facts, title, angle)
    grounded = bool(fact.get("grounded")) and fact.get("risk_level") != "high"
    if not facts:
        grounded = False
        fact["feedback"] = (fact.get("feedback") or "") + " | no verified facts"
    report["layers"]["fact"] = fact
    if not grounded:
        report["reject_reasons"].append(
            f"fact: {fact.get('feedback') or fact.get('invented_claims') or 'not grounded'}"
        )

    # Layer 3: quality
    quality = quality_check(tweet)
    q_score = float(quality.get("score") or 0)
    substance = float(quality.get("substance") or 0)
    human = float(quality.get("human") or 0)
    q_ok = bool(quality.get("approved")) and q_score >= MIN_QUALITY_SCORE and substance >= MIN_SUBSTANCE_SCORE and human >= 6
    report["layers"]["quality"] = quality
    if not q_ok:
        report["reject_reasons"].append(
            f"quality score={q_score} substance={substance} human={human}: {quality.get('feedback')}"
        )

    candidate = tweet
    # One forced rewrite if any gate failed
    if not ok or not grounded or not q_ok:
        rewrite_seed = (quality.get("improved_post") or tweet).strip()
        rewrite_prompt = f"""Rewrite this X post so it passes every rule. Keep the same meaning and stay inside the verified facts.

ORIGINAL:
\"\"\"{tweet}\"\"\"

VERIFIED FACTS:
{chr(10).join('- ' + f for f in facts) if facts else '(none)'}

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

Return ONLY JSON: {{"tweet": "<rewritten post>"}}
"""
        raw_rw = invoke_claude(rewrite_prompt, max_tokens=400, temperature=0.35)
        improved = (extract_json_safe(raw_rw, {"tweet": rewrite_seed}).get("tweet") or rewrite_seed).strip()
        logger.info("Trying rewrite (%d chars): %s", len(improved), improved[:120])
        ok2, viol2 = style_gate(improved, url)
        fact2 = fact_check(improved, facts, title, angle)
        grounded2 = bool(fact2.get("grounded")) and fact2.get("risk_level") != "high" and bool(facts)
        quality2 = quality_check(improved)
        q2 = (
            bool(quality2.get("approved"))
            and float(quality2.get("score") or 0) >= MIN_QUALITY_SCORE
            and float(quality2.get("substance") or 0) >= MIN_SUBSTANCE_SCORE
            and float(quality2.get("human") or 0) >= 6
        )
        report["layers"]["rewrite"] = {
            "tweet": improved,
            "style_passed": ok2,
            "style_violations": viol2,
            "fact": fact2,
            "quality": quality2,
        }
        if ok2 and grounded2 and q2:
            candidate = improved
            ok, grounded, q_ok = True, True, True
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
            report["reject_reasons"] = reasons
            logger.info("Rewrite still failed: %s", reasons)

    if ok and grounded and q_ok:
        # Final style recheck on candidate
        final_ok, final_viol = style_gate(candidate, url)
        if final_ok:
            report["approved"] = True
            report["final_tweet"] = candidate
            report["reject_reasons"] = []
        else:
            report["reject_reasons"] = final_viol
    else:
        report["approved"] = False
        report["final_tweet"] = ""

    return report


def load_drafts(path: Path = DRAFTS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f).get("tweets") or []


def save_outputs(
    approved: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(approved),
        "tweets": approved,
        "note": "Only validation_approved=true items may be posted.",
    }
    with DRAFTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "approved_count": len(approved),
        "reports": reports,
    }
    with REPORT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info(
        "Validation done: %d approved → %s | report → %s",
        len(approved),
        DRAFTS_PATH,
        REPORT_PATH,
    )


def run_validation(drafts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if drafts is None:
        drafts = load_drafts()
    if not drafts:
        logger.warning("No drafts to validate.")
        save_outputs([], [])
        return []

    reports: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []

    for draft in drafts:
        logger.info("Validating: %s", (draft.get("tweet") or "")[:100])
        rep = validate_one(draft)
        reports.append(rep)
        if rep.get("approved") and rep.get("final_tweet"):
            out = dict(draft)
            out["tweet"] = rep["final_tweet"]
            out["char_count"] = len(rep["final_tweet"])
            out["validation_approved"] = True
            out["status"] = "approved"
            out["validation"] = {
                "quality_score": (rep.get("layers") or {}).get("quality", {}).get("score"),
                "fact_score": (rep.get("layers") or {}).get("fact", {}).get("score"),
                "risk_level": (rep.get("layers") or {}).get("fact", {}).get("risk_level"),
            }
            approved.append(out)
            logger.info("APPROVED (%d chars): %s", out["char_count"], out["tweet"])
        else:
            logger.warning("REJECTED: %s", rep.get("reject_reasons"))

    save_outputs(approved, reports)
    return approved


def main() -> int:
    approved = run_validation()
    if not approved:
        logger.warning("Nothing approved — will not post.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
