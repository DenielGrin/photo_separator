#!/usr/bin/env python3
"""Import your own benchmark run JSON into the local UI, without any model/API calls."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_separator.images import prepare
from photo_separator.store import Store


def main(args):
    store = Store(args.db)
    previews = Path(args.db).parent / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    for name in args.runs:
        run = json.loads(Path(name).read_text())
        if store.run(run["run_id"]):
            print("Already present:", run["run_id"])
            continue
        for row in run["results"]:
            cache = Path(args.cache) / (hashlib.sha256(row["url"].encode()).hexdigest() + ".bin")
            if cache.is_file() and "metrics" in row:
                image = prepare(cache.read_bytes())
                if image.digest != row["metrics"]["digest"]:
                    raise ValueError("Frozen image differs from benchmark: " + row["url"])
                (previews / (image.digest + ".jpg")).write_bytes(image.jpeg)
                row["preview_url"] = "/api/previews/" + image.digest
        store.save_run(run)
        print("http://127.0.0.1:8765/?run=" + run["run_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--db", default=os.environ.get("PHOTO_DB", "data/workbench.sqlite3"))
    parser.add_argument("--cache", default="data/image-cache")
    main(parser.parse_args())
