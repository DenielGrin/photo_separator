#!/usr/bin/env python3
"""Discover every zero-price OpenRouter image model; checkpoint a bounded, resumable pilot."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_separator.catalog import discover_free_vision
from photo_separator.images import ImageError, download
from photo_separator.providers import PROMPT, PROMPT_VERSION
from photo_separator.schemas import Policy, ProviderConfig, SelectionRequest
from photo_separator.selection import select
from scripts.benchmark import metrics


class PauseRun(RuntimeError):
    pass


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def invocation_budget(requested, available):
    if isinstance(available, (int, float)) and not isinstance(available, bool):
        return min(requested, max(0, int(available) - 2))
    return min(requested, 40)


def signature(dataset_bytes, candidate):
    return hashlib.sha256(
        dataset_bytes
        + json.dumps(
            {
                "model": candidate["model"],
                "json_mode": candidate["json_mode"],
                "prompt": PROMPT,
                "policy": Policy().model_dump(),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class ModelUnavailable(PauseRun):
    pass


class Journal:
    def __init__(self, path, fingerprint, budget):
        self.path, self.budget, self.calls = path, budget, 0
        self.state = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "signature": fingerprint,
                "attempts": 0,
                "rows": {},
            }
        )
        if self.state["signature"] != fingerprint:
            raise ValueError("Dataset, prompt, policy or model changed; use a fresh output directory")

    def before(self):
        if self.calls >= self.budget:
            raise PauseRun("invocation_budget_exhausted")
        self.calls += 1
        self.state["attempts"] += 1
        save(self.path, self.state)  # count dispatched calls even if the process is interrupted

    def after(self, row):
        previous = self.state["rows"].get(row["url"])
        if previous is not None:
            self.state.setdefault("previous_observations", []).append(previous)
        self.state["rows"][row["url"]] = row
        save(self.path, self.state)
        for code in (403, 429):
            if f"HTTP {code}" in row.get("error", ""):
                raise ModelUnavailable(f"model_http_{code}")
        if any(f"HTTP {code}" in row.get("error", "") for code in (401, 402)):
            raise PauseRun("provider_auth_or_quota_block")


async def main(args):
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    data_bytes = Path(args.dataset).read_bytes()
    dataset = json.loads(data_bytes)
    candidates = await discover_free_vision(require_json=False)
    plan = {
        "checked_at": datetime.now(UTC).isoformat(),
        "catalog_source": "https://openrouter.ai/api/v1/models",
        "dataset_sha256": hashlib.sha256(data_bytes).hexdigest(),
        "prompt_version": PROMPT_VERSION,
        "candidates": candidates,
        "status": "ready" if os.environ.get("OPENROUTER_API_KEY") else "blocked_missing_openrouter_key",
        "completed": [],
        "invocation_max_calls": args.max_calls,
    }
    save(root / "plan.json", plan)
    if args.plan_only or plan["status"] != "ready":
        print(json.dumps({"candidates": len(candidates), "status": plan["status"], "inference_calls": 0}))
        return
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    hashes = {p["url"]: p.get("sha256") for r in dataset for p in r["photos"]}

    def loader(url):
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".bin")
        raw = path.read_bytes() if path.exists() else download(url)
        if hashes[url] and hashlib.sha256(raw).hexdigest() != hashes[url]:
            raise ImageError("Dataset bytes changed: SHA-256 mismatch")
        if not path.exists():
            path.write_bytes(raw)
        return raw

    # Authenticated quota metadata contains no inference; never log account labels or keys.
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        response = await client.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
        )
    if response.status_code != 200:
        plan["status"] = "quota_metadata_unavailable"
        save(root / "plan.json", plan)
        return
    quota = response.json().get("data", {}).get("free_model_daily_requests", {})
    available = quota.get("remaining")
    budget = invocation_budget(args.max_calls, available)
    plan["effective_max_calls"] = budget
    plan["free_model_daily_requests"] = {k: quota.get(k) for k in ("used", "limit", "remaining")}
    remaining = budget
    # Probe every candidate first so a small daily quota does not hide whole model families.
    plan["probes"] = {}
    last_dispatch = [0.0]

    def before(journal):
        delay = 3.2 - (time.monotonic() - last_dispatch[0])
        if delay > 0:
            time.sleep(delay)
        journal.before()
        last_dispatch[0] = time.monotonic()

    for candidate in candidates:
        name = hashlib.sha256(candidate["model"].encode()).hexdigest()[:16]
        folder = root / name
        folder.mkdir(exist_ok=True)
        journal = Journal(folder / "checkpoint.json", signature(data_bytes, candidate), remaining)
        cached = {
            u: r
            for u, r in journal.state["rows"].items()
            if r.get("assessment") or not getattr(args, "retry_errors", False)
        }
        config = ProviderConfig(
            **{k: candidate[k] for k in ("provider", "model", "input_price", "output_price", "json_mode")}
        )
        restaurant = dataset[0]
        request = SelectionRequest(
            restaurant_id=restaurant["restaurant_id"],
            urls=[restaurant["photos"][0]["url"]],
            config=config,
            policy=Policy(),
        )
        previous = journal.state["rows"].get(request.urls[0], {})
        if (
            previous.get("error")
            and not any(f"HTTP {code}" in previous["error"] for code in (403, 429))
            and not getattr(args, "retry_errors", False)
        ):
            plan["probes"][candidate["model"]] = {
                "reason_code": "provider_error",
                "error": previous["error"],
                "assessment_received": False,
                "previous_attempt": True,
            }
            save(root / "plan.json", plan)
            continue
        if not getattr(args, "retry_unavailable", False) and any(
            f"HTTP {code}" in previous.get("error", "") for code in (403, 429)
        ):
            plan["probes"][candidate["model"]] = {
                "reason_code": "provider_error",
                "error": previous["error"],
                "assessment_received": False,
                "previous_attempt": True,
            }
            save(root / "plan.json", plan)
            continue
        try:
            probe = await select(
                request,
                loader=loader,
                cached_results=cached,
                before_assess=lambda: before(journal),
                on_assessed=journal.after,
                concurrency=1,
                reuse_failures=not getattr(args, "retry_errors", False),
            )
            row = probe["results"][0]
            plan["probes"][candidate["model"]] = {
                "reason_code": row["reason_code"],
                "error": row.get("error"),
                "assessment_received": bool(row.get("assessment")),
            }
            print(
                json.dumps(
                    {
                        "probe_model": candidate["model"],
                        "reason_code": row["reason_code"],
                        "error": row.get("error"),
                    }
                ),
                flush=True,
            )
        except ModelUnavailable as exc:
            plan["probes"][candidate["model"]] = {
                "reason_code": "provider_error",
                "error": "Provider HTTP " + str(exc).rsplit("_", 1)[-1],
                "assessment_received": False,
            }
            print(json.dumps({"probe_model": candidate["model"], "status": str(exc)}), flush=True)
        except PauseRun as exc:
            plan.update(status=str(exc), paused_model=candidate["model"])
            save(root / "plan.json", plan)
            return
        remaining -= journal.calls
        save(root / "plan.json", plan)
    for candidate in candidates:
        if not plan["probes"].get(candidate["model"], {}).get("assessment_received"):
            continue
        name = hashlib.sha256(candidate["model"].encode()).hexdigest()[:16]
        folder = root / name
        folder.mkdir(exist_ok=True)
        previous_report = folder / "metrics.json"
        if previous_report.exists() and not getattr(args, "retry_errors", False):
            old = json.loads(previous_report.read_text())
            Journal(folder / "checkpoint.json", signature(data_bytes, candidate), remaining)
            if old.get("dataset_sha256") == plan["dataset_sha256"] and old.get("model") == candidate["model"]:
                plan["completed"].append(candidate["model"])
                save(root / "plan.json", plan)
                continue
        journal = Journal(folder / "checkpoint.json", signature(data_bytes, candidate), remaining)
        # Only successful rows are reused. Failed calls can be retried on the next explicit invocation.
        cached = {
            url: row
            for url, row in journal.state["rows"].items()
            if row.get("assessment") or not getattr(args, "retry_errors", False)
        }
        config = ProviderConfig(
            **{k: candidate[k] for k in ("provider", "model", "input_price", "output_price", "json_mode")}
        )
        runs = []
        try:
            for restaurant in dataset:
                request = SelectionRequest(
                    restaurant_id=restaurant["restaurant_id"],
                    urls=[p["url"] for p in restaurant["photos"]],
                    config=config,
                    policy=Policy(),
                )
                run = await select(
                    request,
                    loader=loader,
                    cached_results=cached,
                    before_assess=lambda: before(journal),
                    on_assessed=journal.after,
                    concurrency=1,
                    reuse_failures=not getattr(args, "retry_errors", False),
                )
                run["resumed"] = any(r.get("cache_hit") for r in run["results"])
                runs.append(run)
                save(folder / (restaurant["restaurant_id"] + ".json"), run)
            report = {
                "model": candidate["model"],
                "json_mode": candidate["json_mode"],
                "dataset_sha256": plan["dataset_sha256"],
                "prompt_version": PROMPT_VERSION,
                "label_status": "provisional_agent_annotation_requires_independent_human_review",
                "metrics": metrics(dataset, runs),
                "latency_comparable": not any(r["resumed"] for r in runs),
            }
            save(folder / "metrics.json", report)
            plan["completed"].append(candidate["model"])
            print(
                json.dumps(
                    {
                        "completed_model": candidate["model"],
                        "photos": report["metrics"]["photos"],
                        "errors": report["metrics"]["error_count"],
                    }
                ),
                flush=True,
            )
        except ModelUnavailable as exc:
            plan.setdefault("unavailable_models", []).append(candidate["model"])
            print(json.dumps({"model": candidate["model"], "status": str(exc)}), flush=True)
        except PauseRun as exc:
            plan.update(status=str(exc), paused_model=candidate["model"])
            save(root / "plan.json", plan)
            print(
                json.dumps({"status": str(exc), "calls_this_invocation": budget - remaining + journal.calls})
            )
            return
        remaining -= journal.calls
        save(root / "plan.json", plan)
    plan["status"] = (
        "finished_with_unavailable_models" if len(plan["completed"]) != len(candidates) else "completed"
    )
    save(root / "plan.json", plan)
    print(json.dumps({"status": plan["status"], "calls_this_invocation": budget - remaining}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="examples/labeled-pilot.json")
    parser.add_argument("--cache", default="data/image-cache")
    parser.add_argument("--output", default="results/free-cloud")
    parser.add_argument("--max-calls", type=int, choices=range(1, 501), default=40)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--retry-unavailable", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    asyncio.run(main(parser.parse_args()))
