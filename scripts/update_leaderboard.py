#!/usr/bin/env python3
"""Generate a precision-first pilot leaderboard from measured, comparable reports."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- leaderboard:start -->", "<!-- leaderboard:end -->"


def ranked(reports, dataset_sha):
    eligible, excluded = [], []
    for report in reports:
        m = report["metrics"]
        if (
            report["dataset_sha256"] == dataset_sha
            and m["photos"] == 20
            and m["error_count"] == 0
            and m["accepted"] > 0
            and m["good_recall"] >= 0.5
        ):
            eligible.append(report)
        else:
            excluded.append(report)
    eligible.sort(
        key=lambda r: tuple(
            -r["metrics"][k] for k in ("cover_precision", "good_recall", "decision_accuracy", "ndcg_at_6")
        )
    )
    return eligible, excluded


def main(free_dir=None):
    reports = [
        json.loads((ROOT / "docs/benchmarks" / name).read_text())
        for name in (
            "qwen2-metrics-v2.json",
            "qwen8-metrics-v2.json",
            "gpt-luna-metrics.json",
            "deepseek-metrics.json",
        )
    ]
    directories = (
        [Path(free_dir)] if free_dir else [ROOT / "docs/benchmarks/free-cloud", ROOT / "results/free-cloud"]
    )
    free_reports = {}
    for directory in directories:
        for path in sorted(directory.glob("*/metrics.json")):
            report = json.loads(path.read_text())
            if report["model"].endswith(":free"):
                free_reports[report["model"]] = report
    reports.extend(free_reports.values())
    dataset_sha = hashlib.sha256((ROOT / "examples/labeled-pilot.json").read_bytes()).hexdigest()
    ordered, excluded = ranked(reports, dataset_sha)
    lines = [
        "| Место | Модель / размещение | Чистота обложек | Найдено хороших | Совпадение решений | Ошибочно приняты | ≥3 фото / рестораны | Расход за 20 фото |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, report in enumerate(ordered, 1):
        m = report["metrics"]
        host = (
            "локально, исторический тест"
            if report["model"].startswith("qwen")
            else ("бесплатное облако" if report["model"].endswith(":free") else "платное облако")
        )
        cost = (
            "$0 API; оборудование отдельно"
            if host.startswith("локально")
            else f"≈${m['estimated_cost_usd']:.6f}"
        )
        lines.append(
            f"| {i} | `{report['model']}` — {host} | {m['cover_precision']:.1%} | {m['good_recall']:.1%} | {m['decision_accuracy']:.1%} | {m['false_accept_count']} | {m['restaurants_with_at_least_3_covers']}/2 | {cost} |"
        )
    lines += ["", "**Вне рейтинга: ошибки или недостаточная полнота отбора:**"]
    for report in excluded:
        m = report["metrics"]
        precision = (
            f"{m['cover_precision']:.0%}"
            if m["cover_precision"] is not None
            else "не определена (нет обложек)"
        )
        cost = (
            "$0 (free API)"
            if report["model"].endswith(":free")
            else f"≥${m['estimated_cost_usd']:.6f} (неполный)"
        )
        lines.append(
            f"- `{report['model']}`: {m['error_count']} ошибок на {m['photos']} фото; приняты {m['accepted']} фото, чистота {precision}, полнота {m['good_recall']:.1%}; ≥3 фото в {m['restaurants_with_at_least_3_covers']}/2 ресторанах; расход {cost}."
        )
    block = "\n".join(lines)
    path = ROOT / "README.md"
    text = path.read_text()
    if START not in text:
        raise ValueError("README leaderboard markers missing")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    path.write_text(before + START + "\n" + block + "\n" + END + after)
    (ROOT / "docs/benchmarks/leaderboard.json").write_text(
        json.dumps(
            {
                "dataset_sha256": dataset_sha,
                "order": [r["model"] for r in ordered],
                "excluded": [r["model"] for r in excluded],
                "sort": [
                    "cover_precision desc",
                    "good_recall desc",
                    "decision_accuracy desc",
                    "ndcg_at_6 desc",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--free-dir", default=None)
    main(parser.parse_args().free_dir)
