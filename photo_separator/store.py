"""SQLite research store. Manual exclusions/pins survive future automatic runs."""

import json
import sqlite3
from pathlib import Path


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, restaurant TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS covers(restaurant TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save_run(self, run):
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?)", (run["run_id"], run["restaurant_id"], json.dumps(run))
            )

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _get(db, restaurant):
        row = db.execute("SELECT payload FROM covers WHERE restaurant=?", (restaurant,)).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "restaurant_id": restaurant,
                "revision": 0,
                "cover": [],
                "pinned": [],
                "excluded": [],
                "candidates": [],
                "known_urls": [],
                "run_id": None,
            }
        )

    def get(self, restaurant):
        with self.connect() as db:
            return self._get(db, restaurant)

    def mutate(self, restaurant, expected_revision, run=None, manual=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._get(db, restaurant)
            if state["revision"] != expected_revision:
                raise Conflict("Revision changed; reload cover before applying changes")
            if run:
                if run["restaurant_id"] != restaurant:
                    raise ValueError("Run belongs to another restaurant")
                # Technical baseline/errors must not erase a previously useful cover.
                if not run["semantic_model_used"] or any(
                    r["reason_code"] in {"provider_error", "fetch_failed"} for r in run["results"]
                ):
                    raise ValueError("Cannot publish a technical-only or incomplete provider run")
                state["candidates"] = list(
                    dict.fromkeys(
                        run["cover"]
                        + [
                            r["url"]
                            for r in sorted(run["results"], key=lambda r: -(r.get("score") or 0))
                            if r["decision"] == "keep"
                        ]
                    )
                )
                state["known_urls"] = list(
                    dict.fromkeys(state["known_urls"] + [r["url"] for r in run["results"] if "metrics" in r])
                )
                state["run_id"] = run["run_id"]
            if manual:
                url, action = manual
                if url not in state["known_urls"]:
                    raise ValueError("Photo must belong to a saved run for this restaurant")
                state["pinned"] = [u for u in state["pinned"] if u != url]
                state["excluded"] = [u for u in state["excluded"] if u != url]
                if action == "pin":
                    if len(state["pinned"]) >= 6:
                        raise ValueError("At most six manually pinned photos allowed")
                    state["pinned"].append(url)
                elif action == "exclude":
                    state["excluded"].append(url)
            state["cover"] = (
                state["pinned"]
                + [u for u in state["candidates"] if u not in state["excluded"] and u not in state["pinned"]]
            )[:6]
            state["revision"] += 1
            db.execute(
                "INSERT INTO covers VALUES(?,?,?) ON CONFLICT(restaurant) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                (restaurant, state["revision"], json.dumps(state)),
            )
            return state
