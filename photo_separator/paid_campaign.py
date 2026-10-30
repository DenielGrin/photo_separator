"""Explicitly authorized paid comparison. Credentials are process-local, costs bounded before dispatch."""

import asyncio
import base64
import hashlib
import json
import math
import time
from pathlib import Path

import httpx

from .campaign import PROMPT, parse_reply, restaurant_result, write_json

MODELS = [
    ("openai", "gpt-6-luna", 0.10, 0.50),
    ("deepseek", "deepseek-flash", 0.30, 1.20),
    ("openrouter", "nvidia/nemotron-3.5-super-vl:free", 0.0, 0.0),
    ("openrouter", "dots-studio/dots-3-note-preview:free", 0.0, 0.0),
    ("openrouter", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", 0.0, 0.0),
]


class NotRun(ValueError):
    """No inference request was dispatched because a budget/quota guard stopped it."""


class Budget:
    def __init__(self, limit, used=0):
        self.limit, self.used = limit, used

    def reserve(self, estimate):
        if estimate < 0 or self.used + estimate > self.limit:
            raise NotRun("Campaign budget exhausted before dispatch")
        self.used += estimate

    def reconcile(self, estimate, actual):
        if actual is not None:
            self.used += actual - estimate


def chunks(restaurant, size=20):
    gallery = [p for p in restaurant["gallery"] if p.get("asset")]
    return [restaurant | {"gallery": gallery[i : i + size]} for i in range(0, len(gallery), size)]


def upper_cost(photos, rates, output=8000):
    # Conservative token envelope for normalized JPEG <=1280 px, plus text and output.
    tokens = 8000 + sum(
        math.ceil(p["metrics"]["width"] / 16) * math.ceil(p["metrics"]["height"] / 16) * 4 + 8192
        for p in photos
    )
    return (tokens * rates[0] + output * rates[1]) / 1e6


def measured_cost(usage, provider, rates):
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    if inp is None or out is None:
        return None
    # Upper tariff, no cache/off-peak credit: conservative estimate, not an invoice.
    return (inp * rates[0] + out * rates[1]) / 1e6


async def run_authorized_campaign(command, credentials, max_cost_usd=5):
    root = Path(command["root"]).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    calls_dir = root / "paid_calls"
    calls_dir.mkdir(exist_ok=True)
    journals = {
        p.name: json.loads(p.read_text())
        for folder in [str(root)] + command.get("budget_roots", [])
        for p in (Path(folder) / "paid_calls").glob("*.json")
    }
    old = list(journals.values())
    budget = Budget(max_cost_usd, sum(c.get("accounted_usd", 0) for c in old))
    lock = asyncio.Lock()
    free_remaining = 0
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        # Freeze free route catalog; no paid routing/fallback is possible for :free.
        resp = await client.get("https://openrouter.ai/api/v1/models")
        catalog = resp.json().get("data", []) if resp.status_code == 200 else []
        allowed = {
            m["id"]
            for m in catalog
            if m["id"].endswith(":free")
            and "image" in m.get("architecture", {}).get("input_modalities", [])
            and all(
                float(m.get("pricing", {}).get(k, 0)) == 0
                for k in ("prompt", "completion", "image", "request")
            )
        }
        write_json(root / "catalog-free.json", [m for m in catalog if m["id"] in allowed])
        if credentials.get("openrouter"):
            resp = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": "Bearer " + credentials["openrouter"]},
            )
            if resp.status_code == 200:
                free_remaining = max(
                    0,
                    int(resp.json().get("data", {}).get("free_model_daily_requests", {}).get("remaining", 0))
                    - 2,
                )
        free_remaining = min(free_remaining, command.get("max_free_calls", 40))
        models = MODELS[:2] + [m for m in MODELS[2:] if m[1] in allowed]
        if command.get("providers"):
            models = [m for m in models if m[0] in command["providers"]]
        if command.get("all_free", True) and (
            not command.get("providers") or "openrouter" in command["providers"]
        ):
            present = {m[1] for m in models}
            models += [("openrouter", mid, 0.0, 0.0) for mid in sorted(allowed) if mid not in present]
        free_gate = asyncio.Lock()
        last_free_start = 0.0
        free_counts = {}
        disabled = set()
        write_json(
            root / "paid_protocol.json",
            {
                "version": "concrete-ids-selection-v4"
                if command.get("concrete_ids")
                else "full-gallery-chunk-reduce-v3",
                "concrete_ids": bool(command.get("concrete_ids")),
                "chunk_size": 20,
                "models": [
                    {"provider": p, "model": m, "input_per_million_usd": i, "output_per_million_usd": o}
                    for p, m, i, o in models
                ],
                "prompt": PROMPT,
                "manifest_sha256": manifest["manifest_sha256"],
                "max_cost_usd": max_cost_usd,
                "cost_basis": "upper tariff times measured usage; unknown billable calls retain reservation",
                "free_call_budget": free_remaining,
                "current_covers_in_selection": False,
                "budget_roots": command.get("budget_roots", []),
                "per_model_free_limits": command.get("per_model_free_limits", {}),
            },
        )

        async def request(provider, model, rates, restaurant, stage):
            nonlocal free_remaining, last_free_start
            photos = restaurant["gallery"]
            ident = hashlib.sha256(
                json.dumps(
                    [model, restaurant["restaurant_id"], stage, [p["input_sha256"] for p in photos]]
                ).encode()
            ).hexdigest()[:24]
            if command.get("concrete_ids"):
                ident = hashlib.sha256(("concrete-ids-v4:" + ident).encode()).hexdigest()[:24]
            path = calls_dir / (ident + ".json")
            if path.exists():
                rec = json.loads(path.read_text())
                if rec.get("status") == "completed":
                    return parse_reply(rec["response_text"], [restaurant])[restaurant["restaurant_id"]]
                raise ValueError("Previous call failed; no automatic duplicate billing")
            if provider in disabled or model in disabled:
                raise NotRun("Provider disabled after authentication/billing error")
            if provider == "openrouter":
                async with free_gate:
                    if free_counts.get(model, 0) >= command.get("per_model_free_limits", {}).get(
                        model, command.get("max_free_per_model", 10)
                    ):
                        raise NotRun("Per-model free request budget exhausted")
                    await asyncio.sleep(max(0, 3.3 - (time.monotonic() - last_free_start)))
                    last_free_start = time.monotonic()
                    free_counts[model] = free_counts.get(model, 0) + 1
            estimate = upper_cost(photos, rates)
            async with lock:
                if provider in disabled or model in disabled:
                    raise NotRun("Provider or model disabled before dispatch")
                if provider == "openrouter":
                    if free_remaining <= 0:
                        raise NotRun("Free request budget exhausted")
                    free_remaining -= 1
                budget.reserve(estimate)
            prompt = PROMPT
            if command.get("concrete_ids"):
                prompt = prompt.replace('"123"', json.dumps(restaurant["restaurant_id"]))
                prompt = prompt.replace("R123-P001", photos[0]["photo_id"]).replace(
                    "R123-P002", photos[min(1, len(photos) - 1)]["photo_id"]
                )
                prompt += (
                    "\nRequired restaurant_id: "
                    + json.dumps(restaurant["restaurant_id"])
                    + ". Exact permitted photo IDs: "
                    + json.dumps([p["photo_id"] for p in photos])
                    + ". Copy these actual IDs; include every one exactly once."
                )
            parts = [{"type": "text", "text": prompt}]
            for p in photos:
                asset = (root / p["asset"]).resolve()
                if asset.parent != (root / "assets").resolve():
                    raise ValueError("Invalid asset path")
                raw = asset.read_bytes()
                if hashlib.sha256(raw).hexdigest() != p["input_sha256"]:
                    raise ValueError("Input checksum mismatch")
                parts += [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"restaurant_id": restaurant["restaurant_id"], "photo_id": p["photo_id"]}
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + base64.b64encode(raw).decode(),
                            "detail": "high",
                        },
                    },
                ]
            rec = {
                "provider": provider,
                "model": model,
                "restaurant_id": restaurant["restaurant_id"],
                "stage": stage,
                "input_photo_ids": [p["photo_id"] for p in photos],
                "status": "dispatched_unknown",
                "accounted_usd": estimate,
                "reservation_usd": estimate,
                "estimated_cost_usd": None,
                "usage": None,
            }
            write_json(path, rec)
            started = time.monotonic()
            try:
                if provider == "openai":
                    content = [
                        {"type": "input_text", "text": p["text"]}
                        if p["type"] == "text"
                        else {"type": "input_image", "image_url": p["image_url"]["url"], "detail": "high"}
                        for p in parts
                    ]
                    payload = {
                        "model": model,
                        "reasoning": {"effort": "low"},
                        "text": {"format": {"type": "json_object"}},
                        "max_output_tokens": 8000,
                        "input": [{"role": "user", "content": content}],
                    }
                    url = "https://api.openai.com/v1/responses"
                else:
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": parts}],
                        "max_tokens": 8000,
                        "response_format": {"type": "json_object"},
                    }
                    if provider == "deepseek":
                        payload["thinking"] = {"type": "disabled"}
                    url = (
                        "https://api.deepseek.com/chat/completions"
                        if provider == "deepseek"
                        else "https://openrouter.ai/api/v1/chat/completions"
                    )
                response = await client.post(
                    url, headers={"Authorization": "Bearer " + credentials[provider]}, json=payload
                )
                rec["http_status"] = response.status_code
                if response.status_code in {401, 402}:
                    disabled.add(provider)
                elif response.status_code in {403, 404, 429}:
                    disabled.add(model)
                if response.status_code != 200:
                    raise ValueError("Provider HTTP " + str(response.status_code))
                value = response.json()
                usage = value.get("usage", {})
                actual = measured_cost(usage, provider, rates)
                if provider == "openrouter" and float(usage.get("cost") or 0) != 0:
                    disabled.add(provider)
                    raise ValueError("Unexpected nonzero free-route cost")
                rec.update(usage=usage, estimated_cost_usd=actual)
                async with lock:
                    budget.reconcile(estimate, actual)
                if actual is not None:
                    rec["accounted_usd"] = actual
                if provider == "openai":
                    text = "".join(
                        c.get("text", "")
                        for o in value.get("output", [])
                        if o.get("type") == "message"
                        for c in o.get("content", [])
                        if c.get("type") == "output_text"
                    )
                else:
                    text = value["choices"][0]["message"].get("content") or ""
                # Persist only model JSON about public photographs, never provider errors/credentials.
                rec["response_text"] = text
                parsed = parse_reply(text, [restaurant])[restaurant["restaurant_id"]]
                rec["status"] = "completed"
                return parsed
            except Exception as exc:
                rec.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                rec["seconds"] = round(time.monotonic() - started, 3)
                write_json(path, rec)
                print(
                    json.dumps(
                        {
                            k: rec.get(k)
                            for k in [
                                "model",
                                "restaurant_id",
                                "stage",
                                "status",
                                "http_status",
                                "estimated_cost_usd",
                                "seconds",
                            ]
                        }
                    ),
                    flush=True,
                )
                if provider == "openrouter":
                    await asyncio.sleep(3.3)

        async def model_run(provider, model, i, o):
            rows = sorted(
                (r for r in manifest["restaurants"] if r["status"] == "ready"),
                key=lambda r: len(r["gallery"]),
            )
            folder = root / "model_results"
            folder.mkdir(exist_ok=True)
            semaphore = asyncio.Semaphore(1 if provider == "openrouter" else 3)

            async def one(r):
                path = folder / (
                    hashlib.sha256((model + r["restaurant_id"]).encode()).hexdigest()[:24] + ".json"
                )
                if path.exists():
                    return
                rec = {
                    "model": model,
                    "restaurant_id": r["restaurant_id"],
                    "status": "failed",
                    "gallery_inputs": r["gallery_inputs"],
                }
                try:
                    groups = chunks(r)
                    outputs = []
                    for n, g in enumerate(groups):
                        outputs.append(await request(provider, model, (i, o), g, "chunk-" + str(n)))
                    candidates = {p.photo_id for a in outputs for p in a.selected}
                    finalists = [p for p in r["gallery"] if p["photo_id"] in candidates]
                    level = 0
                    final_assessments = {}
                    while len(finalists) > 6:
                        reduced = []
                        for n, g in enumerate(chunks(r | {"gallery": finalists})):
                            a = await request(provider, model, (i, o), g, f"final-{level}-{n}")
                            final_assessments.update({p.photo_id: p for p in a.selected})
                            ids = {p.photo_id for p in a.selected}
                            reduced += [p for p in g["gallery"] if p["photo_id"] in ids]
                        if len(reduced) >= len(finalists):
                            raise ValueError("Reduction did not converge")
                        finalists = reduced
                        level += 1
                    # Final selection assessments come from the latest evaluation for each finalist.
                    final_ids = {p["photo_id"] for p in finalists}
                    parsed_by_id = {p.photo_id: p for a in outputs for p in a.selected}
                    parsed_by_id.update(final_assessments)
                    from .campaign import RemainderAssessment, RestaurantAssessment

                    selected = [parsed_by_id[p["photo_id"]] for p in finalists]
                    skipped = {p.photo_id: p for a in outputs for p in a.remainder}
                    remainder = [
                        skipped.get(
                            p["photo_id"],
                            RemainderAssessment(
                                photo_id=p["photo_id"],
                                disposition="not_selected",
                                reason="Outside final top six",
                            ),
                        )
                        for p in r["gallery"]
                        if p.get("asset") and p["photo_id"] not in final_ids
                    ]
                    assessment = RestaurantAssessment(
                        restaurant_id=r["restaurant_id"], selected=selected, remainder=remainder
                    )
                    rec.update(
                        status="completed",
                        result=restaurant_result(r, assessment),
                        model_selected_ids=[p.photo_id for p in selected],
                    )
                except NotRun as exc:
                    rec.update(status="not_run", error_type=type(exc).__name__)
                except Exception as exc:
                    rec["error_type"] = type(exc).__name__
                write_json(path, rec)
                # Keep the demo table current while the campaign proceeds.
                from scripts.real_cover_report import render

                render(root)

            async def bounded(r):
                async with semaphore:
                    await one(r)

            await asyncio.gather(*(bounded(r) for r in rows))
            return model

        await asyncio.gather(*(model_run(*m) for m in models))
    write_json(
        root / "budget.json",
        {
            "limit_usd": max_cost_usd,
            "accounted_upper_usd": budget.used,
            "note": "Usage-based upper estimate, not provider invoice. Failed unknown calls retain reservation.",
        },
    )
    print("CAMPAIGN FINISHED", flush=True)
