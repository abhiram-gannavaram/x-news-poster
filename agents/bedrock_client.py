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

_RETRYABLE_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
    "ModelErrorException",
    "ModelNotReadyException",
    "RequestTimeout",
    "TooManyRequestsException",
}


def get_client():
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    return boto3.client("bedrock-runtime", region_name=region)


def _is_retryable(exc: Exception) -> bool:
    code = ""
    if hasattr(exc, "response") and exc.response:
        code = exc.response.get("Error", {}).get("Code", "") or ""
    if code in _RETRYABLE_CODES:
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "throttl",
            "timeout",
            "temporar",
            "unavailable",
            "empty bedrock",
            "rate",
        )
    )


def invoke_claude(
    prompt: str,
    *,
    model_id: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    retries: int = 5,
) -> str:
    model_id = model_id or os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
    client = get_client()
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }

    delays = [1, 2, 4, 8, 16]
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
        except (ClientError, BotoCoreError, RuntimeError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries - 1 and _is_retryable(exc):
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning("Bedrock retryable error (%s), sleep %ss", exc, delay)
                time.sleep(delay)
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
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise TypeError(f"Expected JSON object, got {type(data).__name__}")
    return data


def extract_json_safe(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        data = extract_json(text)
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")
        return data
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error("JSON parse failed: %s | raw=%s", exc, (text or "")[:300])
        return fallback
