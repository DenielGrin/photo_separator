#!/usr/bin/env python3
"""Score already saved predictions against a versioned label set. No model calls."""

import argparse
import hashlib
import json
from pathlib import Path

from benchmark import metrics


def main(args):
    dataset_path = Path(args.dataset)
    dataset = json.loads(dataset_path.read_text())
    runs = [json.loads(Path(p).read_text()) for p in args.runs]
    report = {
        "evaluation_mode": "offline_rescore_no_model_calls",
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "label_status": "provisional_agent_annotation_requires_independent_human_review",
        "model": runs[0]["model"],
        "runs": [r["run_id"] for r in runs],
        "metrics": metrics(dataset, runs),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="examples/labeled-pilot.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("runs", nargs="+")
    main(parser.parse_args())
