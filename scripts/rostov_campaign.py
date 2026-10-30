#!/usr/bin/env python3
"""Prepare/run/report a real-cover campaign; model requests are OpenRouter :free only."""

import argparse
import asyncio
import csv
import html
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_separator.campaign import (
    VERSION,
    batch_plan,
    campaign_summary,
    prepare_campaign,
    run_campaign,
    write_json,
)

DEFAULT_MODELS = [
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3.5-super-vl:free",
]


def render_report(manifest, root, models):
    root = Path(root)
    summary = campaign_summary(manifest, root, models)
    write_json(root / "summary.json", summary)
    calls = [json.loads(p.read_text()) for p in (root / "calls").glob("*.json")]
    outputs = {
        (r["restaurant_id"], c["model"]): r
        for c in calls
        if c["status"] == "completed"
        for r in c["restaurants"]
    }
    failures = {
        (rid, c["model"]): c.get("error", c["status"])
        for c in calls
        if c["status"] != "completed"
        for rid in c["restaurant_ids"]
    }
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731

    def photo(p, caption):
        if not p.get("asset"):
            return '<p class="error">Фото недоступно: ' + esc(p.get("fetch_error", "нет данных")) + "</p>"
        return f'<figure><a target="_blank" rel="noopener" href="{esc(p["url"])}"><img loading="lazy" src="{esc(p["asset"])}" alt="{esc(caption)}"></a><figcaption>{esc(caption)}</figcaption></figure>'

    cards = []
    csv_rows = []
    for restaurant in sorted(
        manifest["restaurants"],
        key=lambda r: (r["status"] != "ready", r.get("listing_rank") or 10**9, r["restaurant_id"]),
    ):
        rid = restaurant["restaurant_id"]
        before = (
            "".join(photo(p, ", ".join(p["surfaces"])) for p in restaurant["baselines"])
            or "<p>Обложка не зафиксирована</p>"
        )
        after = []
        for model in models:
            result = outputs.get((rid, model))
            if result:
                selected = set(result["cover"])
                previews = "".join(photo(p, p["photo_id"]) for p in result["results"] if p["url"] in selected)
                text = f"{len(selected)} обложек; недобор до трёх: {result['shortfall']}"
                content = previews or "<p>Автоматических кандидатов нет</p>"
                detail = "".join(
                    f"<li>{esc(p['photo_id'])}: {esc(p['decision'])} · {esc(p['reason_code'])} · {esc((p.get('assessment') or {}).get('reason', p.get('model_reason', p.get('fetch_error', ''))))}</li>"
                    for p in result["results"]
                )
                content += f"<details><summary>Решения по каждому фото</summary><ul>{detail}</ul></details>"
                status = "completed"
            else:
                text = failures.get((rid, model), "Ожидает галерею / бесплатную квоту / запуск")
                content = '<p class="pending">Результата модели пока нет</p>'
                status = "error" if (rid, model) in failures else "pending"
            after.append(
                f'<div class="model"><h3>{esc(model)}</h3><p>{esc(text)}</p><div class="tiles">{content}</div></div>'
            )
            csv_rows.append(
                {
                    "restaurant_id": rid,
                    "name": restaurant["name"],
                    "source_url": restaurant["url"],
                    "model": model,
                    "status": status,
                    "gallery_count": len(restaurant["gallery"]),
                    "before_urls": " | ".join(p["url"] for p in restaurant["baselines"]),
                    "after_urls": " | ".join(result["cover"]) if result else "",
                    "preference": "",
                    "after_acceptable": "",
                    "reviewer": "",
                    "reviewer_type": "",
                    "notes": "",
                }
            )
        gallery = "".join(photo(p, p["photo_id"]) for p in restaurant["gallery"])
        cards.append(
            f'<section class="restaurant" id="r-{esc(rid)}"><h2><a href="{esc(restaurant["url"])}" target="_blank" rel="noopener">{esc(restaurant["name"])}</a> <small>ID {esc(rid)}</small></h2><p>{len(restaurant["gallery"])} фото в зафиксированной галерее; успешно загружено {restaurant["gallery_inputs"]}. Native app: обложка не проверена.</p><div class="comparison"><div><h3>До · наблюдаемая веб-обложка</h3>{before}</div>{"".join(after)}</div><details><summary>Вся зафиксированная галерея и исходные ссылки</summary><div class="gallery">{gallery or "Галерея ещё не получена"}</div></details></section>'
        )
    metrics = "".join(
        f"<tr><td>{esc(m['model'])}</td><td>{m['completed_restaurants']}</td><td>{m['restaurants_at_least_three']}</td><td>{m['failed_calls']}</td><td>не измерено</td></tr>"
        for m in summary["models"]
    )
    document = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ростов: реальные обложки до и после</title><style>
body{font-family:system-ui,sans-serif;background:#f3f3ed;color:#26362e;margin:0;padding:32px}main{max-width:1800px;margin:auto}h1{font-size:36px;margin-bottom:12px}h2{font-size:25px}h3{font-size:17px;overflow-wrap:anywhere}a{color:inherit}small{font-size:14px;color:#667068}.notice{border-left:4px solid #a07322;padding:14px;background:#fff8df;line-height:1.6}.restaurant{margin-top:40px;padding-top:15px;border-top:2px solid #9aa89a}.comparison{display:grid;grid-template-columns:repeat(4,minmax(220px,1fr));gap:25px;overflow-x:auto}figure{margin:0 0 12px;max-width:240px}img{width:100%;height:155px;object-fit:contain;background:#dfe4d9}figcaption{font-size:12px;overflow-wrap:anywhere}.tiles{display:flex;flex-wrap:wrap;gap:8px}.tiles figure{width:calc(50% - 8px)}.tiles img{height:110px}details{padding:12px 0;line-height:1.5}.gallery{display:flex;flex-wrap:wrap;gap:18px;margin-top:15px}.gallery figure{width:160px}li{margin-bottom:8px}.pending{color:#6b716b}.error{color:#8b332c}table{border-collapse:collapse;width:100%;font-size:15px;margin:20px 0}td,th{text-align:left;padding:12px;border-bottom:1px solid #ccd2c5;overflow-wrap:anywhere}input{font:inherit;padding:10px;width:320px;max-width:90%;margin:20px 0}@media(max-width:800px){body{padding:15px}.comparison{grid-template-columns:repeat(4,260px)}}
</style><main><h1>Ростов-на-Дону: обложки «до / после»</h1>"""
    document += f"<p>Протокол {esc(manifest.get('protocol', VERSION))}. Снимок источника: {esc(manifest['source'].get('capture_date', 'не указан'))}.</p>"
    document += f'<div class="notice"><strong>Статус исследования, не готовый рейтинг.</strong><br>Цель: {summary["target_restaurants"]} ресторанов; получено {summary["collected_restaurants"]}; с загруженной галереей: {summary["ready_restaurants"]}. Общий завершённый набор всех моделей: {summary["common_completed_restaurants"]}.<br>Пустое «после» означает отсутствие результата, а не плохую модель. Победитель и доля улучшений не определены без проверки результатов. Баллы, которые модель поставила собственным фото, не доказывают улучшение.<br>Web-обложки могут быть коллажами или уменьшенными изображениями. Сопоставление их численных оценок с оригиналами не является независимой оценкой качества. Обложки native-приложения не подтверждены.</div>'
    document += (
        "<table><thead><tr><th>Модель</th><th>Готово ресторанов</th><th>≥3 обложки</th><th>Сбойных запросов</th><th>Качество улучшения</th></tr></thead><tbody>"
        + metrics
        + "</tbody></table>"
    )
    document += '<p><a href="manifest.json">Зафиксированный manifest</a> · <a href="summary.json">Сводка</a> · <a href="comparison.csv">Таблица CSV и поля для независимой оценки</a></p><input id="filter" placeholder="Найти ресторан по названию / ID" aria-label="Фильтр ресторанов">'
    document += "".join(cards)
    document += """</main><script>document.getElementById('filter').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('.restaurant').forEach(r=>r.hidden=!r.querySelector('h2').textContent.toLowerCase().includes(q))});</script></html>"""
    (root / "comparison.html").write_text(document)
    with (root / "comparison.csv").open("w", newline="") as f:
        if csv_rows:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            for row in csv_rows:
                writer.writerow(
                    {
                        k: ("'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v)
                        for k, v in row.items()
                    }
                )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "plan", "run", "report"])
    parser.add_argument("--source")
    parser.add_argument("--output", default="results/rostov-2026-10-30")
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--max-calls", type=int, choices=range(1, 501), default=40)
    parser.add_argument("--max-images", type=int, choices=range(1, 81), default=44)
    parser.add_argument("--restaurants-per-request", type=int, choices=range(1, 5), default=2)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Explicitly allow a smoke test below the target sample; not a completed campaign",
    )
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    models = args.models.split(",")
    if args.action == "prepare":
        if not args.source:
            parser.error("--source is required")
        manifest = prepare_campaign(json.loads(Path(args.source).read_text()), root, args.limit)
        print(
            json.dumps(
                {
                    "collected": len(manifest["restaurants"]),
                    "ready": sum(r["status"] == "ready" for r in manifest["restaurants"]),
                }
            )
        )
        render_report(manifest, root, models)
        return
    manifest = json.loads((root / "manifest.json").read_text())
    if args.action == "plan":
        batches = batch_plan(manifest["restaurants"], args.max_images, args.restaurants_per_request)
        planned = len(batches) * len(models)
        print(
            json.dumps(
                {
                    "ready_restaurants": sum(len(b) for b in batches),
                    "models": len(models),
                    "planned_calls": planned,
                    "days_at_48_calls": math.ceil(planned / 48),
                    "target_restaurants": manifest["target_restaurants"],
                }
            )
        )
    elif args.action == "run":
        ready = sum(r["status"] == "ready" for r in manifest["restaurants"])
        if ready < manifest["target_restaurants"] and not args.allow_partial:
            raise SystemExit(
                "Insufficient frozen sources for target campaign. Collect sources first, or explicitly --allow-partial for a smoke test."
            )
        result = asyncio.run(
            run_campaign(
                manifest,
                root,
                models,
                os.environ.get("OPENROUTER_API_KEY", ""),
                args.max_calls,
                args.max_images,
                args.restaurants_per_request,
            )
        )
        write_json(root / "last-invocation.json", result)
        print(json.dumps(result))
    print(json.dumps(render_report(manifest, root, models), ensure_ascii=False))


if __name__ == "__main__":
    main()
