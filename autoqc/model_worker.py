"""OpenAI-compatible video-model adapter for the AutoQC pull worker.

This module deliberately contains runtime endpoint details, not database mode
configuration.  The database stores a mode identity (for example
``qwenA3b4fps``); this adapter maps that identity to the service supplied by
the worker environment.

Usage with ``worker_client.py``::

    python worker_client.py --url http://10.12.46.7:8810 \
      --adapter autoqc.model_worker:infer --models qwenA3b4fps \
      --worker-id qwen-worker-1

The model is asked for JSON only.  The adapter validates and normalises the
three labels before returning them to the API.  It never reads human labels
from the testset payload.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


LABELS = ("bad_product", "bad_movement", "nudity")

# These are worker-runtime mappings, intentionally outside the modes table.
# Environment variables can override them for a staging or local endpoint.
MODEL_SERVICES = {
    "qwenA3b4fps": {
        "base_url": "http://prod.dev.internal/llm",
        "model": "Qwen3.6-35B-A3B",
        # Qwen3.6's vLLM integration uses mm_processor_kwargs for video
        # sampling. Its documented default is 2 FPS.
        "sampling_field": "mm_processor_kwargs",
        "default_fps": 2.0,
    },
    # Legacy mode name retained for previously created benchmark snapshots.
    "qwenllm2fps": {
        "base_url": "http://prod.dev.internal/llm",
        "model": "Qwen3.6-35B-A3B",
        "sampling_field": "mm_processor_kwargs",
        "default_fps": 2.0,
    },
    "cosmos4fps": {
        "base_url": "http://prod.dev.internal/cosmos",
        "model": "Cosmos3-Super",
        # Cosmos3's OpenAI-compatible reasoner uses media_io_kwargs. NVIDIA's
        # recommended reasoner input rate is 4 FPS.
        "sampling_field": "media_io_kwargs",
        "default_fps": 4.0,
    },
}


def _service_for(mode: str, runtime: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]]:
    """Return (base URL, served model) for a database mode name."""
    try:
        service = MODEL_SERVICES[mode]
    except KeyError as exc:
        raise ValueError(f"No worker endpoint mapping exists for mode {mode!r}") from exc
    env_key = re.sub(r"[^A-Za-z0-9]", "_", mode).upper()
    runtime = runtime or {}
    base_url = str(runtime.get("endpoint") or os.getenv(f"AUTOQC_{env_key}_URL", service["base_url"])).rstrip("/")
    model = str(runtime.get("served_model") or os.getenv(f"AUTOQC_{env_key}_MODEL", service["model"]))
    return base_url, model, service


def _sampling_for(mode: str, service: dict[str, Any], runtime: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the model-specific frame-sampling body and an audit description.

    FPS is worker runtime configuration, not a mode-table setting.  Set
    ``AUTOQC_QWENA3B4FPS_FPS`` or ``AUTOQC_COSMOS4FPS_FPS`` in the worker
    environment.  ``*_NUM_FRAMES`` is an alternative and cannot be combined
    with FPS.  The returned audit object is saved as inference evidence.
    """
    env_key = re.sub(r"[^A-Za-z0-9]", "_", mode).upper()
    runtime = runtime or {}
    # A benchmark snapshot supplied by the API is authoritative. Environment
    # variables remain a fallback for workers used outside the AutoQC API.
    fps_value = runtime.get("fps") if "fps" in runtime else os.getenv(f"AUTOQC_{env_key}_FPS")
    frame_value = runtime.get("num_frames") if "num_frames" in runtime else os.getenv(f"AUTOQC_{env_key}_NUM_FRAMES")
    if fps_value and frame_value:
        raise ValueError(f"Set only one of AUTOQC_{env_key}_FPS or AUTOQC_{env_key}_NUM_FRAMES")
    if fps_value:
        try:
            fps = float(fps_value)
        except ValueError as exc:
            raise ValueError(f"AUTOQC_{env_key}_FPS must be a positive number") from exc
        if fps <= 0:
            raise ValueError(f"AUTOQC_{env_key}_FPS must be a positive number")
        sampling = {"fps": fps}
        audit = {"fps": fps, "source": "benchmark_snapshot" if runtime else "worker_environment"}
    elif frame_value:
        try:
            num_frames = int(frame_value)
        except ValueError as exc:
            raise ValueError(f"AUTOQC_{env_key}_NUM_FRAMES must be a positive integer") from exc
        if num_frames < 1:
            raise ValueError(f"AUTOQC_{env_key}_NUM_FRAMES must be a positive integer")
        sampling = {"num_frames": num_frames}
        audit = {"num_frames": num_frames, "source": "benchmark_snapshot" if runtime else "worker_environment"}
    else:
        fps = float(service["default_fps"])
        sampling = {"fps": fps}
        audit = {"fps": fps, "source": "adapter_default"}
    field = service["sampling_field"]
    if field == "mm_processor_kwargs":
        # Sample once in vLLM's media reader, then tell Qwen's processor to
        # consume those frames as-is. Sampling in both layers can produce a
        # timestamp/grid mismatch on Qwen3.6. Keep the fps in both places so
        # the processor can reconstruct timestamps correctly.
        processor = {"do_sample_frames": False}
        if "fps" in sampling:
            processor["fps"] = sampling["fps"]
        body = {
            "media_io_kwargs": {"video": sampling},
            "mm_processor_kwargs": processor,
        }
    else:
        body = {field: {"video": sampling}}
    return body, audit


def _as_json(text: str) -> dict[str, Any]:
    """Parse a JSON object, accepting a fenced or surrounding explanation."""
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Model response did not contain a JSON object")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Model response must be a JSON object")
    return value


def _normalise_label(value: Any, name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    # Be tolerant of models returning JSON strings, but reject arbitrary text.
    if isinstance(value, str):
        normal = value.strip().lower()
        if normal in {"true", "yes", "1"}:
            return True
        if normal in {"false", "no", "0"}:
            return False
        if normal in {"null", "unknown", "uncertain", "not_applicable"}:
            return None
    raise ValueError(f"Invalid value for {name}: expected true, false, or null")


def _prompt(recipe: dict[str, Any]) -> str:
    definitions = recipe.get("label_definitions") or {}
    definition_text = json.dumps(definitions, ensure_ascii=False, sort_keys=True)
    return (
        (recipe.get("prompt") or "Inspect the supplied product video for AutoQC issues.").strip()
        + "\n\n"
        + "Label definitions (use only what is visibly supported by the video): "
        + definition_text
        + "\nReturn exactly one JSON object with this shape, and no markdown: "
        + '{"labels":{"bad_product":true|false|null,"bad_movement":true|false|null,'
        + '"nudity":true|false|null},"evidence":{"timestamps":[],"explanation":""}}'
        + "\nUse null when the video does not provide enough evidence."
    )


def _messages(item: dict[str, Any], recipe: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = item.get("metadata") or {}
    # Do not pass tags, provenance, or review decisions to the model.  The API
    # intentionally omits those from the worker item; this is an extra guard
    # when an adapter is called directly in a development shell.
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"labels", "set", "provenance", "decision", "review"}
    }
    reference_urls: list[str] = []
    # New records carry all product references.  Accept the older singular
    # image_url field as a fallback so previously frozen testsets continue to
    # work unchanged.
    for value in (safe_metadata.get("reference_image_urls"), safe_metadata.get("image_urls")):
        if isinstance(value, list):
            reference_urls.extend(value)
    if not reference_urls and isinstance(safe_metadata.get("image_url"), str):
        reference_urls.append(safe_metadata["image_url"])
    reference_urls = list(dict.fromkeys(
        url for url in reference_urls
        if isinstance(url, str) and url.startswith(("http://", "https://", "data:"))
    ))
    safe_metadata["reference_image_count"] = len(reference_urls)
    # Do not duplicate a potentially large URL array in the text prompt; the
    # actual image content blocks below are what the multimodal model sees.
    safe_metadata.pop("reference_image_urls", None)
    safe_metadata.pop("image_urls", None)
    text = "Product/reference metadata (context, not ground truth):\n" + json.dumps(
        safe_metadata, ensure_ascii=False, sort_keys=True
    )
    content: list[dict[str, Any]] = [
        {"type": "text", "text": text},
        {"type": "video_url", "video_url": {"url": item["video_url"]}},
    ]
    if reference_urls:
        content.append({
            "type": "text",
            "text": (
                f"Reference product images ({len(reference_urls)} total). "
                "Compare the product shown in the video with all of them; "
                "do not decide from only the first image."
            ),
        })
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in reference_urls)
    return [
        {"role": "system", "content": _prompt(recipe)},
        {"role": "user", "content": content},
    ]


def _extract(response: dict[str, Any]) -> dict[str, Any]:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("OpenAI-compatible response had no assistant content") from exc
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    if not isinstance(content, str):
        raise ValueError("Assistant content was not text")
    payload = _as_json(content)
    raw_labels = payload.get("labels", payload)
    if not isinstance(raw_labels, dict):
        raise ValueError("Model JSON has no labels object")
    labels = {name: _normalise_label(raw_labels.get(name), name) for name in LABELS}
    evidence = payload.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {"explanation": str(evidence)}
    # Keep evidence useful but bounded before it is stored in JSONB.
    evidence = {str(k): v for k, v in evidence.items()}
    if "explanation" in evidence:
        evidence["explanation"] = str(evidence["explanation"])[:4000]
    return {"labels": labels, "evidence": evidence}


def infer(item: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """Run one video through the mode selected by the worker client."""
    mode = recipe.get("mode")
    if not isinstance(mode, str):
        raise ValueError("Worker recipe did not include a mode name")
    runtime = recipe.get("worker_settings") or {}
    base_url, model, service = _service_for(mode, runtime)
    sampling_body, sampling_audit = _sampling_for(mode, service, runtime)
    payload = {
        "model": model,
        "messages": _messages(item, recipe),
        "temperature": 0,
        "max_tokens": int(os.getenv("AUTOQC_LLM_MAX_TOKENS", "500")),
        # Qwen3 reasoning otherwise consumes the completion budget in a
        # hidden `reasoning` field and leaves `message.content` null.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    payload.update(sampling_body)
    headers = {"X-Drivetrain-Priority": os.getenv("AUTOQC_LLM_PRIORITY", "experimental")}
    api_key = os.getenv("AUTOQC_LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Do not impose a client-side deadline by default. The shared gateway may
    # queue requests behind other users; the worker heartbeat is independent of
    # this inference call. Set AUTOQC_LLM_TIMEOUT explicitly if desired.
    timeout_value = os.getenv("AUTOQC_LLM_TIMEOUT", "").strip().lower()
    timeout = None if timeout_value in {"", "0", "none", "null"} else float(timeout_value)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(f"{base_url}/v1/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        result = _extract(response.json())
        result["evidence"].setdefault("video_sampling", sampling_audit)
        return result
