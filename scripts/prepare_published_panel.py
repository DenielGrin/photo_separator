#!/usr/bin/env python3
"""Restore the published frozen demo from public CDN URLs and saved observations, without inference."""

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from photo_separator.campaign import prepare_campaign, write_json
from photo_separator.images import download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--without-observations", action="store_true")
    args = parser.parse_args()
    sourcepath = Path(args.source)
    source = json.loads(sourcepath.read_text())
    root = Path(args.root)
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    urls = {p["url"] for r in source["restaurants"] for p in r["gallery"]} | {
        v["declared_url"] for r in source["restaurants"] for v in r["baselines"].values()
    }

    def fetch(url):
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".bin")
        if not path.exists():
            path.write_bytes(download(url))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, urls))
    manifest = prepare_campaign(source, root, len(source["restaurants"]))
    expected_path = sourcepath.parent / "manifest.json"
    if expected_path.exists():
        expected = json.loads(expected_path.read_text())
        wanted = {p["photo_id"]: p for r in expected["restaurants"] for p in r["gallery"] + r["baselines"]}
        for r in manifest["restaurants"]:
            for p in r["gallery"] + r["baselines"]:
                if p.get("sha256") != wanted[p["photo_id"]].get("sha256") or p.get("input_sha256") != wanted[
                    p["photo_id"]
                ].get("input_sha256"):
                    raise ValueError(
                        "Frozen image bytes changed for "
                        + p["photo_id"]
                        + "; refuse to label this a reproduction"
                    )
    if not args.without_observations:
        for folder in ["paid_calls", "model_results", "judge_calls"]:
            (root / folder).mkdir(exist_ok=True)
        for c in json.loads((sourcepath.parent / "calls.json").read_text()):
            if c["kind"] not in {"paid_calls", "judge_calls"}:
                raise ValueError("Invalid observation kind")
            if c["study"] != "panel_v4":
                continue
            if not re.fullmatch(r"[0-9a-f]{24}", c["call_id"]):
                raise ValueError("Invalid saved call ID")
            write_json(
                root / c["kind"] / (c["call_id"] + ".json"),
                {k: v for k, v in c.items() if k not in ["study", "kind", "call_id"]},
            )
        for result in json.loads((sourcepath.parent / "model-results.json").read_text()):
            name = (
                hashlib.sha256((result["model"] + result["restaurant_id"]).encode()).hexdigest()[:24]
                + ".json"
            )
            write_json(root / "model_results" / name, result)
        for name in ["paid_protocol.json", "judge_protocol.json", "catalog-free.json"]:
            path = sourcepath.parent / name
            if path.exists():
                write_json(root / name, json.loads(path.read_text()))
    from scripts.real_cover_report import render

    print(json.dumps(render(root), ensure_ascii=False))


if __name__ == "__main__":
    main()
