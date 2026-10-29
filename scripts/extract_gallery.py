#!/usr/bin/env python3
"""Extract original photo metadata from a locally saved Restaurant Guru HTML page.

Read a page supplied by the operator; never evaluate embedded JavaScript.
Usage: python scripts/extract_gallery.py page.html --output data/gallery.json
"""

import argparse
import json
from pathlib import Path


def extract(text):
    marker = '"photos":[{'
    offset = text.find(marker)
    if offset < 0:
        raise ValueError("Embedded original photo list not found; page format may have changed")
    start = offset + len('"photos":')
    photos, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(photos, list):
        raise ValueError("Unexpected photo list format")
    return [
        {k: p[k] for k in ("src", "id", "width", "height") if k in p}
        for p in photos
        if isinstance(p, dict) and isinstance(p.get("src"), str) and p["src"].startswith("https://")
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    photos = extract(Path(args.html).read_text())
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(photos, ensure_ascii=False, indent=2) + "\n")
    print(f"Extracted {len(photos)} original photo references; no images downloaded")
