"""Public metadata discovery only: no credentials, images or inference requests."""

from decimal import Decimal, InvalidOperation

import httpx

CATALOG_URL = "https://openrouter.ai/api/v1/models"


def free_vision_models(payload: dict, require_json: bool = True) -> list[dict]:
    results = []
    for model in payload.get("data", []):
        if not isinstance(model, dict):
            continue
        model_id = model.get("id", "")
        architecture = model.get("architecture") or {}
        if not isinstance(model_id, str) or not model_id.endswith(":free"):
            continue
        if "image" not in architecture.get("input_modalities", []):
            continue
        # Default UI requires JSON mode; the full CLI can validate prompt-only JSON.
        if require_json and "response_format" not in (model.get("supported_parameters") or []):
            continue
        pricing = model.get("pricing") or {}
        try:
            values = [Decimal(str(pricing[k])) for k in ("prompt", "completion")]
            values += [Decimal(str(pricing.get(k, "0"))) for k in ("image", "request")]
            if any(not value.is_finite() or value != 0 for value in values):
                continue
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
        results.append(
            {
                "id": "or-live-" + model_id,
                "provider": "openrouter",
                "model": model_id,
                "label": "Облако · OpenRouter free · " + str(model.get("name", model_id)),
                "input_price": 0,
                "output_price": 0,
                "context_length": model.get("context_length"),
                "catalog_verified_free": True,
                "json_mode": "object"
                if "response_format" in (model.get("supported_parameters") or [])
                else "prompt",
            }
        )
    return sorted(results, key=lambda item: item["model"])


async def discover_free_vision(require_json: bool = True):
    async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
        response = await client.get(CATALOG_URL)
        response.raise_for_status()
        payload = response.json()
    return free_vision_models(payload, require_json=require_json)
