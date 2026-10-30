#!/usr/bin/env python3
"""Import operator/browser-captured public DOM photo metadata without executing site JS."""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from photo_separator.campaign import prepare_campaign, write_json
from photo_separator.images import download
from scripts.extract_gallery import extract


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--galleries", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text())
    observed = json.loads(Path(args.galleries).read_text())
    good = {r["restaurant_id"]: r for r in observed if r.get("scripts")}
    for r in source["restaurants"]:
        d = good.get(r["restaurant_id"])
        if not d:
            continue
        try:
            photos = extract("\n".join(d["scripts"]))
        except (ValueError, json.JSONDecodeError):
            r["gallery_source"] = {
                "url": d["url"],
                "capture_error": "Embedded gallery was truncated; no partial gallery passed to models",
            }
            continue
        r["gallery"] = [
            {"photo_id": p.get("id"), "url": p["src"], "width": p.get("width"), "height": p.get("height")}
            for p in dict((p["src"], p) for p in photos).values()
        ]
        r["gallery_source"] = {
            "url": d["url"],
            "title": d["title"],
            "method": "Public embedded original photo list read from browser DOM, not executed",
            "displayed_count": d.get("photo_count"),
        }
    root = Path(args.root)
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    write_json(root / "source.json", source)
    urls = {p["url"] for r in source["restaurants"] for p in r.get("gallery", [])} | {
        v["declared_url"]
        for r in source["restaurants"]
        for v in r.get("baselines", {}).values()
        if not v["declared_url"].endswith("log.gif")
    }

    def fetch(url):
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".bin")
        if path.exists():
            return
        try:
            path.write_bytes(download(url))
        except Exception:
            pass  # prepare_campaign records the bounded downloader failure.

    with ThreadPoolExecutor(max_workers=6) as pool:
        for n, f in enumerate(as_completed([pool.submit(fetch, url) for url in urls]), 1):
            f.result()
            if n % 200 == 0:
                print("Public images processed", n, "/", len(urls), flush=True)
    old = root / "manifest.json"
    if old.exists():
        value = json.loads(old.read_text())
        write_json(root / "manifest_history" / (value["manifest_sha256"] + ".json"), value)
    manifest = prepare_campaign(source, root, 50)
    from scripts.real_cover_report import render

    render(root)
    print(
        json.dumps(
            {
                "galleries": sum(r["status"] == "ready" for r in manifest["restaurants"]),
                "photos": sum(r["gallery_inputs"] for r in manifest["restaurants"]),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
