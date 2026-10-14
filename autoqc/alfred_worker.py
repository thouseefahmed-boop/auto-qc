"""AutoQC inference through the Cosmos-capable Alfred in MiniVET newArch.

The AutoQC API owns benchmark claims, leases and result persistence. This
adapter performs one model call only; it does not submit nested queue jobs.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from alfred import CosmosServer, LLMServer

from autoqc.model_worker import (
    LABELS, _as_json, _extract, _messages, _sampling_for, _service_for,
)


class _PriorityHeaders:
    """Keep drivetrain's admission priority on Alfred's HTTP session."""

    def _get_session(self):
        session = super()._get_session()
        session.headers["X-Drivetrain-Priority"] = os.getenv(
            "AUTOQC_LLM_PRIORITY", "experimental"
        )
        return session


class _CosmosServer(_PriorityHeaders, CosmosServer):
    pass


class _LLMServer(_PriorityHeaders, LLMServer):
    pass


def _api_url(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    return endpoint if endpoint.endswith("/v1") else endpoint + "/v1"


async def _infer(item: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    mode = recipe.get("mode")
    if not isinstance(mode, str):
        raise ValueError("Worker recipe did not include a mode name")
    runtime = recipe.get("worker_settings") or {}
    endpoint, model, service = _service_for(mode, runtime)
    sampling, audit = _sampling_for(mode, service, runtime)
    is_cosmos = mode == "cosmos4fps"
    timeout_value = os.getenv(
        "AUTOQC_LLM_TIMEOUT", "900" if is_cosmos else "200"
    ).strip().lower()
    timeout = None if timeout_value in {"", "0", "none", "null"} else float(timeout_value)
    server_type = _CosmosServer if is_cosmos else _LLMServer
    kwargs = {"video_fps": None} if is_cosmos else {}
    server = server_type(
        _api_url(endpoint), model=model,
        api_key=os.getenv("AUTOQC_LLM_API_KEY"), temperature=0,
        max_tokens=int(os.getenv("AUTOQC_LLM_MAX_TOKENS", "2048")),
        max_concurrent=1, timeout=timeout, retries=0, **kwargs,
    )
    messages = _messages(item, recipe)
    if is_cosmos:
        # AutoQC video URLs are extensionless /api/media/<uuid> paths. Preserve
        # their explicit video_url type and Cosmos's media-before-text order;
        # Alfred's filename-based media inference would identify them as images.
        content = messages[1]["content"]
        messages[1]["content"] = (
            [block for block in content if block["type"] != "text"]
            + [block for block in content if block["type"] == "text"]
        )
    extra = dict(sampling)
    if not is_cosmos:
        extra["chat_template_kwargs"] = {"enable_thinking": False}
    try:
        message = await server.respond(messages, extra=extra)
        result = _extract({"choices": [{"message": message}]})
        raw = _as_json(message["content"] if isinstance(message["content"], str) else
                       "".join(b.get("text", "") for b in message["content"] if isinstance(b, dict)))
        labels = raw.get("labels", raw)
        if any(label not in labels for label in LABELS):
            raise ValueError("Model response omitted a required AutoQC label")
        result["evidence"]["video_sampling"] = audit
        result["evidence"]["inference_backend"] = "minivet_alfred"
        result["evidence"]["served_model"] = model
        return result
    finally:
        await server.close()


def infer(item: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """Synchronous entry point for worker_client's heartbeat-enabled worker."""
    return asyncio.run(_infer(item, recipe))
