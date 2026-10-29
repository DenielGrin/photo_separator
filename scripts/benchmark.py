#!/usr/bin/env python3
"""Run identical frozen image bytes through each model; never send labels to a model."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_separator import selection
from photo_separator.images import download
from photo_separator.providers import PRESETS
from photo_separator.schemas import Policy, ProviderConfig, SelectionRequest


def metrics(dataset, runs):
    labels = {(r["restaurant_id"], p["url"]): p for r in dataset for p in r["photos"]}
    total = good = bad = accepted = true_accept = false_accept = false_reject = deferred = errors = (
        correct
    ) = 0
    selected_total = selected_good = 0
    ndcgs = []
    for run in runs:
        for row in run["results"]:
            label = labels[(run["restaurant_id"], row["url"])]["expected"]
            total += 1
            correct += row["decision"] == label
            good += label == "keep"
            bad += label == "reject"
            accepted += row["decision"] == "keep"
            true_accept += row["decision"] == "keep" and label == "keep"
            # Provisional review labels must not be auto-published either.
            false_accept += row["decision"] == "keep" and label != "keep"
            false_reject += row["decision"] == "reject" and label == "keep"
            deferred += row["decision"] == "review"
            errors += row["reason_code"] in {"provider_error", "fetch_failed"}
        selected_total += len(run["cover"])
        selected_good += sum(labels[(run["restaurant_id"], u)]["expected"] == "keep" for u in run["cover"])
        grades = [labels[(run["restaurant_id"], u)].get("grade", 0) for u in run["cover"]]
        ideal = sorted(
            [
                p.get("grade", 0)
                for restaurant in dataset
                if restaurant["restaurant_id"] == run["restaurant_id"]
                for p in restaurant["photos"]
            ],
            reverse=True,
        )[:6]

        def dcg(values):
            return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(values))

        ndcgs.append(dcg(grades) / dcg(ideal) if dcg(ideal) else None)
    precision = true_accept / accepted if accepted else None
    lower = None
    if accepted:
        z = 1.96
        lower = (
            precision
            + z * z / (2 * accepted)
            - z * math.sqrt(precision * (1 - precision) / accepted + z * z / (4 * accepted * accepted))
        ) / (1 + z * z / accepted)
    latencies = sorted(r["latency_ms"] for r in runs)
    measured_ndcg = [x for x in ndcgs if x is not None]
    return {
        "photos": total,
        "decision_accuracy": correct / total if total else None,
        "good_labels": good,
        "bad_labels": bad,
        "accepted": accepted,
        "accept_precision": precision,
        "accept_precision_wilson95_lower": lower,
        "false_accept_count": false_accept,
        "good_recall": true_accept / good if good else None,
        "false_reject_count": false_reject,
        "review_rate": deferred / total if total else None,
        "cover_precision": selected_good / selected_total if selected_total else None,
        "ndcg_at_6": sum(measured_ndcg) / len(measured_ndcg) if measured_ndcg else None,
        "error_count": errors,
        "restaurants_with_at_least_3_covers": sum(len(r["cover"]) >= 3 for r in runs),
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_ms": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None,
        "estimated_cost_usd": sum(r["estimated_cost_usd"] for r in runs),
        "cost_complete": all(r["cost_complete"] for r in runs),
    }


async def main(args):
    dataset_bytes = Path(args.dataset).read_bytes()
    dataset = json.loads(dataset_bytes)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    expected_hashes = {p["url"]: p.get("sha256") for r in dataset for p in r["photos"]}

    def cached_fetch(url):
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".bin")
        if not path.exists():
            path.write_bytes(download(url))
        raw = path.read_bytes()
        expected = expected_hashes.get(url)
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            from photo_separator.images import ImageError

            raise ImageError("Dataset image changed: SHA-256 mismatch; review manifest before benchmarking")
        return raw

    reports = []
    for preset_id in args.presets.split(","):
        preset = next((p for p in PRESETS if p["id"] == preset_id), None)
        if not preset:
            raise SystemExit("Unknown preset: " + preset_id)
        config = ProviderConfig(
            **{k: preset[k] for k in ("provider", "model", "input_price", "output_price")}
        )
        runtime = {}
        if config.provider == "ollama":
            try:
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
                    version = (await client.get(base + "/api/version")).json()
                    tags = (await client.get(base + "/api/tags")).json()
                runtime = {
                    "ollama_version": version.get("version"),
                    "model_digest": next(
                        (m.get("digest") for m in tags.get("models", []) if m.get("name") == config.model),
                        None,
                    ),
                }
            except (httpx.HTTPError, ValueError):
                runtime = {"version_lookup": "unavailable"}
        runs = []
        for restaurant in dataset:
            print(preset_id, restaurant["restaurant_id"], flush=True)
            request = SelectionRequest(
                restaurant_id=restaurant["restaurant_id"],
                urls=[p["url"] for p in restaurant["photos"]],
                config=config,
                policy=Policy(),
            )
            run = await selection.select(request, loader=cached_fetch)
            runs.append(run)
            (output / f"{preset_id}-{restaurant['restaurant_id']}.json").write_text(
                json.dumps(run, ensure_ascii=False, indent=2)
            )
        report = {
            "runtime": runtime,
            "preset": preset_id,
            "model": config.model,
            "metrics": metrics(dataset, runs),
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "label_status": "provisional_agent_annotation_requires_independent_human_review",
            "runs": [r["run_id"] for r in runs],
        }
        reports.append(report)
        (output / f"{preset_id}-metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report["metrics"], ensure_ascii=False), flush=True)
    (output / "comparison.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="examples/labeled-pilot.json")
    parser.add_argument("--presets", default="technical")
    parser.add_argument("--cache", default="data/image-cache")
    parser.add_argument("--output", default="results/pilot")
    asyncio.run(main(parser.parse_args()))
