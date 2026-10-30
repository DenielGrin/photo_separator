#!/usr/bin/env python3
"""Freeze a controlled <=20-photo pool per restaurant; disclose source truncation explicitly."""

import argparse
import hashlib
import json
import random
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from photo_separator.campaign import prepare_campaign, write_json
from photo_separator.images import download
from scripts.extract_gallery import extract


def decode_observed_photos(text):
    try:
        return extract(text), True
    except (ValueError, json.JSONDecodeError):
        pass
    marker = '"photos":[{'
    offset = text.find(marker)
    if offset < 0:
        return [], False
    offset += len('"photos":[')
    decoder = json.JSONDecoder()
    photos = []
    while offset < len(text):
        while offset < len(text) and text[offset] in " \t\r\n,":
            offset += 1
        if text[offset : offset + 1] == "]":
            break
        try:
            value, length = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            break
        if isinstance(value, dict) and str(value.get("src", "")).startswith("https://"):
            photos.append({k: value[k] for k in ("src", "id", "width", "height") if k in value})
        offset += length
    return photos, False


def sample_indices(count, rid, limit=20):
    if count <= limit:
        return list(range(count))
    rng = random.Random("rostov-cover-panel-v4:" + rid)
    return [rng.randrange(i * count // limit, (i + 1) * count // limit) for i in range(limit)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--reuse-root", required=True)
    args = parser.parse_args()
    base = Path(args.capture_dir)
    root = Path(args.root)
    reuse = Path(args.reuse_root)
    source = json.loads((base / "source.json").read_text())
    # Sponsored listings rotate; the controlled panel uses ranked cards only.
    extras_path = base / "extra-cards.json"
    extras = json.loads(extras_path.read_text()) if extras_path.exists() else []
    seen = {r["restaurant_id"] for r in source["restaurants"]}
    ranked = [r for r in source["restaurants"] if r.get("listing_rank")]
    ranked += [r for r in extras if r["restaurant_id"] not in seen]
    source["restaurants"] = ranked[:50]
    source["cohort_rule"] = (
        "First 50 distinct ranked listing cards with observable cover elements across pages 1–3. Rotating sponsored Boemi excluded from this panel; its full-gallery pilot retained separately."
    )
    lazy_path = base / "lazy-cover-evidence.json"
    lazy = {r["restaurant_id"]: r for r in json.loads(lazy_path.read_text())} if lazy_path.exists() else {}
    from photo_separator.baseline_frames import carousel_frames, visible_crop

    for r in source["restaurants"]:
        e = r["baselines"]["listing_visible"]
        if e["declared_url"].endswith("log.gif") and r["restaurant_id"] in lazy:
            a = lazy[r["restaurant_id"]]["img_attrs"]
            e["declared_url"] = a.get("data-src") or a["src"]
            e["crop"] = visible_crop(e["image_style"], float(e["sprite_attributes"]["data-collage-ratio"]))
            e["crop_method"] = (
                "Reconstructed from observed inline CSS and DOM-declared collage URL; full sprite is not the visible cover."
            )
    good = {
        r["restaurant_id"]: r for r in json.loads((base / "galleries.json").read_text()) if r.get("scripts")
    }
    french = json.loads((base / "french-gallery.json").read_text())
    for r in source["restaurants"]:
        d = good.get(r["restaurant_id"])
        if r["url"] == french["url"]:
            d = french
        if d:
            photos, complete = decode_observed_photos("\n".join(d["scripts"]))
            photos = list({p["src"]: p for p in photos}.values())
            indices = sample_indices(len(photos), r["restaurant_id"])
            r["gallery"] = [
                {
                    "url": photos[i]["src"],
                    "photo_id": photos[i].get("id"),
                    "source_index_zero_based": i,
                    "width": photos[i].get("width"),
                    "height": photos[i].get("height"),
                }
                for i in indices
            ]
            r["gallery_source"] = {
                "url": d["url"],
                "title": d["title"],
                "gallery_total_metadata": len(photos),
                "metadata_complete": complete,
                "reported_count": d.get("photo_count"),
                "sample_size": len(indices),
                "sample_method": "One seeded random position from each of 20 equal index strata over captured metadata; all photos when <=20",
                "seed": "rostov-cover-panel-v4:" + r["restaurant_id"],
                "capture_limitation": None
                if complete
                else "DOM source text limited to 200000 characters; sampling uses the completely decoded prefix. Full gallery coverage is NOT claimed.",
            }
        else:
            r["gallery"] = []
            r["gallery_source"] = {"url": r["url"], "capture_error": "Waiting for browser source access"}
    for r in source["restaurants"]:
        original = r["baselines"]["listing_visible"]
        if not original["declared_url"].endswith("log.gif"):
            r["baselines"] = {
                "listing_carousel_" + str(i): e for i, e in enumerate(carousel_frames(original))
            }
    source["study"] = "rostov-cover-panel-v4"
    source["sampling_protocol"] = (
        "At most 20 frozen gallery candidates per restaurant, same bytes for every model. This is a controlled sampled comparison, not exhaustive gallery coverage."
    )
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    write_json(root / "source.json", source)
    urls = {p["url"] for r in source["restaurants"] for p in r["gallery"]} | {
        b["declared_url"]
        for r in source["restaurants"]
        for b in r["baselines"].values()
        if not b["declared_url"].endswith("log.gif")
    }
    failures = {}

    def fetch(url):
        name = hashlib.sha256(url.encode()).hexdigest() + ".bin"
        path = cache / name
        old = reuse / "cache" / name
        if path.exists():
            return
        try:
            if old.exists():
                shutil.copyfile(old, path)
            else:
                path.write_bytes(download(url))
        except Exception:
            failures[url] = {
                "url": url,
                "fetch_error": "Bounded public-image download failed during panel preparation",
            }

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, urls))
    prepare_campaign(source, root, 50, failed_downloads=failures)
    # Model observations are not copied: v4 uses concrete IDs in its prompt.
    from scripts.real_cover_report import render

    print(json.dumps(render(root), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
