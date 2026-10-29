import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from .images import ImageError, download, prepare
from .providers import PROMPT_VERSION, ProviderError, assess, cost_estimate, credential
from .schemas import Assessment, Policy, SelectionRequest

WORK_SLOTS = asyncio.Semaphore(4)


def decide(a: Assessment, metrics: dict, policy: Policy):
    score = round(a.quality * 0.4 + a.appeal * 0.35 + a.relevance * 0.25, 2)
    if min(metrics["width"], metrics["height"]) < policy.min_short_side:
        return "reject", score, "resolution_too_small"
    if a.unsafe:
        return "reject", score, "unsafe_content"
    if a.category in {"menu", "receipt", "people"} or a.text_dominant or a.people == "prominent":
        return "reject", score, "unsuitable_subject"
    if policy.people_policy == "strict" and a.people != "none":
        return "reject", score, "people_policy"
    if a.severe_defect:
        return "reject", score, "severe_visual_defect"
    if a.confidence < policy.min_confidence or a.category == "other":
        return "review", score, "uncertain_assessment"
    if policy.people_policy == "review_background" and a.people == "background":
        return "review", score, "background_people"
    if score < policy.min_score or a.quality < 60 or a.relevance < 60:
        return "reject", score, "below_quality_threshold"
    return "keep", score, "cover_candidate"


def choose_covers(results, policy):
    candidates = sorted(
        (r for r in results if r["decision"] == "keep"), key=lambda r: (-r["score"], r["index"])
    )
    selected = []
    if policy.diversity:
        for category in ("interior", "exterior", "food", "drink"):
            candidate = next((r for r in candidates if r["assessment"]["category"] == category), None)
            if candidate:
                selected.append(candidate)
    selected = selected[: policy.max_covers]
    for item in candidates:
        if len(selected) >= policy.max_covers:
            break
        if item not in selected:
            selected.append(item)
    selected.sort(key=lambda r: (-r["score"], r["index"]))
    return [r["url"] for r in selected]


async def select(request: SelectionRequest, loader=download, preview_dir: Path | None = None):
    credential(request.config)  # configuration failures are detected before downloading any photo
    started = time.monotonic()
    semaphore = asyncio.Semaphore(2)
    prepared = {}
    results = []
    # Fetch/decode one image at a time to cap memory; model calls are concurrent below.
    for index, url in enumerate(request.urls):
        row = {
            "index": index,
            "url": url,
            "decision": "review",
            "score": None,
            "assessment": None,
            "usage": {},
            "cost_estimate_usd": None,
            "latency_ms": 0,
            "reason_code": "fetch_failed",
        }
        try:
            async with WORK_SLOTS:
                image = await asyncio.to_thread(lambda u=url: prepare(loader(u)))
            row["metrics"] = image.metrics()
            if preview_dir is not None:
                await asyncio.to_thread((preview_dir / (image.digest + ".jpg")).write_bytes, image.jpeg)
                row["preview_url"] = "/api/previews/" + image.digest
            exact = next((j for j, im in prepared.items() if im.digest == image.digest), None)
            if exact is not None:
                row.update(decision="reject", reason_code="exact_duplicate", duplicate_of=exact)
            elif min(image.width, image.height) < request.policy.min_short_side:
                row.update(decision="reject", reason_code="resolution_too_small")
            else:
                # dHash is only a review signal. It cannot safely prove semantic duplication.
                near = next(
                    (
                        j
                        for j, im in prepared.items()
                        if image.edge_variance > 20
                        and im.edge_variance > 20
                        and (im.dhash ^ image.dhash).bit_count() <= 3
                    ),
                    None,
                )
                if near is not None:
                    row["possible_duplicate_of"] = near
                prepared[index] = image
        except ImageError as exc:
            row["error"] = str(exc)
        results.append(row)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False
    ) as client:

        async def evaluate(index, image):
            row = results[index]
            t0 = time.monotonic()
            async with semaphore, WORK_SLOTS:
                try:
                    if request.config.provider == "technical":
                        row.update(
                            reason_code="technical_baseline",
                            usage={"input_tokens": 0, "output_tokens": 0},
                            cost_estimate_usd=0,
                        )
                        return
                    output = await assess(request.config, image, client)
                    a = output.assessment
                    decision, score, reason = decide(a, row["metrics"], request.policy)
                    if decision == "keep" and "possible_duplicate_of" in row:
                        decision, reason = "review", "possible_duplicate"
                    row.update(
                        assessment=a.model_dump(),
                        decision=decision,
                        score=score,
                        reason_code=reason,
                        usage=output.usage,
                        resolved_model=output.resolved_model,
                        serving_provider=output.serving_provider,
                        cost_estimate_usd=cost_estimate(request.config, output.usage),
                    )
                except ProviderError as exc:
                    row.update(decision="review", reason_code="provider_error", error=str(exc))
                finally:
                    row["latency_ms"] = round((time.monotonic() - t0) * 1000)

        await asyncio.gather(*(evaluate(i, im) for i, im in prepared.items()))
    cover = choose_covers(results, request.policy)
    calls = [r for r in results if r.get("assessment") or r["reason_code"] == "provider_error"]
    costs = [r["cost_estimate_usd"] for r in calls]
    return {
        "input_signature": hashlib.sha256(
            json.dumps(
                [(r["url"], r.get("metrics", {}).get("digest")) for r in results], ensure_ascii=True
            ).encode()
        ).hexdigest(),
        "run_id": uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(),
        "restaurant_id": request.restaurant_id,
        "provider": request.config.provider,
        "model": request.config.model,
        "prompt_version": PROMPT_VERSION,
        "policy": request.policy.model_dump(),
        "results": results,
        "cover": cover,
        "forward_urls": cover,
        "rejected_urls": [r["url"] for r in results if r["decision"] == "reject"],
        "review_urls": [r["url"] for r in results if r["decision"] == "review"],
        "shortfall": max(0, 3 - len(cover)),
        "latency_ms": round((time.monotonic() - started) * 1000),
        "estimated_cost_usd": round(sum(v for v in costs if v is not None), 8),
        "cost_complete": all(v is not None for v in costs),
        "billing_note": "Estimate from reported tokens and configured rates; excludes unknown failed-call charges, caching discounts, infrastructure and taxes.",
        "semantic_model_used": request.config.provider != "technical",
    }
