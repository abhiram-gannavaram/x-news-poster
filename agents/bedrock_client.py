"""Shared Amazon Bedrock (Claude) helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("bedrock_client")

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def get_client():
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    return boto3.client("bedrock-runtime", region_name=region)


def invoke_claude(
    prompt: str,
    *,
    model_id: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    retries: int = 4,
) -> str:
    model_id = model_id or os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
    client = get_client()
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }

    delays = [1, 2, 4, 8]
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            logger.info("Bedrock invoke model=%s attempt=%d", model_id, attempt + 1)
            resp = client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body),
            )
            payload = json.loads(resp["body"].read())
            parts = payload.get("content") or []
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            if not text.strip():
                raise RuntimeError(f"Empty Bedrock response: {payload!r}")
            return text.strip()
        except (ClientError, BotoCoreError, RuntimeError) as exc:
            last_err = exc
            code = ""
            if hasattr(exc, "response") and exc.response:
                code = exc.response.get("Error", {}).get("Code", "")
            if code in {"ThrottlingException", "ServiceUnavailableException"} and attempt < retries - 1:
                time.sleep(delays[min(attempt, len(delays) - 1)])
                continue
            if attempt < retries - 1 and "throttl" in str(exc).lower():
                time.sleep(delays[min(attempt, len(delays) - 1)])
                continue
            raise
    raise RuntimeError(f"Bedrock failed after retries: {last_err}")


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


def extract_json_safe(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        return extract_json(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error("JSON parse failed: %s | raw=%s", exc, text[:300])
        return fallback
