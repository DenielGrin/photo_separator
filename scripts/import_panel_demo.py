#!/usr/bin/env python3
"""Import frozen Rostov examples into the local API workbench; no inference or cover publication."""

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from photo_separator.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--db", default="data/workbench.sqlite3")
    args = parser.parse_args()
    root = Path(args.root)
    store = Store(args.db)
    previews = Path(args.db).parent / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    records = [json.loads(p.read_text()) for p in (root / "model_results").glob("*.json")]
    calls = [json.loads(p.read_text()) for p in (root / "paid_calls").glob("*.json")]
    demos = {}
    for rid in ["123719605", "109489618"]:
        source = next(
            r
            for r in records
            if r["restaurant_id"] == rid and r["model"] == "gpt-6-luna" and r["status"] == "completed"
        )
        rows = source["result"]["results"]
        if rid == "109489618":
            wanted = {f"R{rid}-P{i:03}" for i in [8, 11, 12, 15, 16, 17, 4, 9, 13, 18]}
            rows = [r for r in rows if r["photo_id"] in wanted]
        results = []
        for index, row in enumerate(rows):
            row = dict(row)
            row["index"] = index
            if row["decision"] == "not_selected":
                row.update(decision="review", reason_code="outside_top_six_not_a_rejection")
            row["error"] = row.get("model_reason", row.get("reason_code", ""))
            if row.get("asset"):
                digest = row["metrics"]["digest"]
                shutil.copyfile(root / row["asset"], previews / (digest + ".jpg"))
                row["preview_url"] = "/api/previews/" + digest
            results.append(row)
        call = next(c for c in calls if c["restaurant_id"] == rid and c["model"] == "gpt-6-luna")
        runid = hashlib.sha256(("rostov-v4-demo:" + rid).encode()).hexdigest()[:32]
        selected = [u for u in source["result"]["cover"] if any(r["url"] == u for r in results)]
        run = {
            "run_id": runid,
            "restaurant_id": rid,
            "created_at": datetime.now(UTC).isoformat(),
            "provider": "openai",
            "model": "gpt-6-luna",
            "prompt_version": "concrete-ids-selection-v4",
            "json_mode": "object",
            "results": results,
            "cover": selected,
            "forward_urls": selected,
            "rejected_urls": [p["url"] for p in results if p["decision"] == "reject"],
            "review_urls": [p["url"] for p in results if p["decision"] == "review"],
            "shortfall": max(0, 3 - len(selected)),
            "latency_ms": round(call["seconds"] * 1000),
            "estimated_cost_usd": call["estimated_cost_usd"],
            "cost_complete": call["estimated_cost_usd"] is not None,
            "semantic_model_used": True,
            "input_signature": hashlib.sha256(
                json.dumps([r["input_sha256"] for r in results]).encode()
            ).hexdigest(),
            "research_note": "Saved observation, no new API call. Nakatilnya displays 10 of 20 originally assessed gallery candidates; cost and latency refer to original 20-photo request. Unselected suitable candidates map to review for legacy workbench compatibility. No human gold labels or cover publication implied.",
        }
        if not store.run(runid):
            store.save_run(run)
        demos[rid] = {"run_id": runid, "url": "http://127.0.0.1:8765/?run=" + runid, "photos": len(results)}
    (root / "service-demos.json").write_text(json.dumps(demos, ensure_ascii=False, indent=2))
    print(json.dumps(demos, ensure_ascii=False))


if __name__ == "__main__":
    main()
