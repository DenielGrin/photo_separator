#!/usr/bin/env python3
"""Compact evidence table: visible listing frame versus model-selected gallery originals."""

import csv
import html
import json
import statistics
from pathlib import Path

from photo_separator.campaign import write_json
from photo_separator.paid_campaign import MODELS


def render(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    results = [json.loads(p.read_text()) for p in (root / "model_results").glob("*.json")]
    lookup = {(r["restaurant_id"], r["model"]): r for r in results}

    def esc(x):
        return html.escape(str(x), quote=True)

    protocol_path = root / "paid_protocol.json"
    protocol = json.loads(protocol_path.read_text()) if protocol_path.exists() else {}
    models = list(
        dict.fromkeys(
            [m[1] for m in MODELS]
            + [m["model"] for m in protocol.get("models", [])]
            + [r["model"] for r in results]
        )
    )
    names = {
        "gpt-6-luna": "GPT-6 Luna",
        "deepseek-flash": "DeepSeek Flash",
        "nvidia/nemotron-3.5-super-vl:free": "Nemotron Super VL · free",
        "dots-studio/dots-3-note-preview:free": "Dots 3 · free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": "Nemotron Nano · free",
    }

    from photo_separator.cover_judge import summarize_judge

    audit = summarize_judge(root)

    def image(p, before=False, verdict=None):
        if not p.get("asset"):
            return '<span class="muted">Фото недоступно</span>'
        crop = (
            next((v["crop"] for v in p.get("evidence", {}).values() if v.get("crop")), None)
            if before
            else None
        )
        style = ""
        if (
            crop
            and all(isinstance(crop.get(k), (int, float)) for k in ("left", "top", "width", "height"))
            and crop["width"] > 0
            and crop["height"] > 0
        ):
            style = f"width:{100 / crop['width']:.5f}%;height:{100 / crop['height']:.5f}%;left:{-100 * crop['left'] / crop['width']:.5f}%;top:{-100 * crop['top'] / crop['height']:.5f}%;object-fit:fill;position:absolute"
        title = esc(p.get("photo_id", "Фото"))
        badge = ""
        if verdict and verdict["verdict"] != "keep":
            label = "×" if verdict["verdict"] == "reject" else "?"
            badge = f'<span class="badge {esc(verdict["verdict"])}" title="ИИ-проверка: {esc(verdict["reason"])}">{label}</span>'
        return f'<a class="photo" href="{esc(p["url"])}" target="_blank" rel="noopener" title="{title} · открыть источник"><img loading="lazy" src="{esc(p["asset"])}" alt="{title}" style="{style}">{badge}</a>'

    names.update({m: m.split("/")[-1].replace(":free", " · free") for m in models if m not in names})
    stats = []
    rows = []
    csvrows = []
    for model in models:
        records = [r for r in results if r["model"] == model]
        completed = [r for r in records if r["status"] == "completed"]
        calls = [json.loads(p.read_text()) for p in (root / "paid_calls").glob("*.json")]
        mc = [c for c in calls if c["model"] == model]
        costs = sum(c.get("estimated_cost_usd") or 0 for c in mc)
        stats.append(
            {
                "model": model,
                "completed_restaurants": len(completed),
                "failed_restaurants": sum(r["status"] == "failed" for r in records),
                "not_run_restaurants": sum(r["status"] == "not_run" for r in records),
                "at_least_three": sum(len(r["result"]["cover"]) >= 3 for r in completed),
                "cover_count": sum(len(r["result"]["cover"]) for r in completed),
                "usage_estimated_upper_usd": costs,
                "calls": len(mc),
                "median_call_seconds": round(statistics.median(c["seconds"] for c in mc), 1) if mc else None,
                "quality_rank": None,
            }
        )
    for r in sorted(
        manifest["restaurants"],
        key=lambda r: (r["status"] != "ready", not bool(r["baselines"]), r.get("listing_rank") or 10**9),
    ):
        rid = r["restaurant_id"]
        ready = r["status"] == "ready"
        reviewed = audit["restaurants"].get(rid, {})
        before = (
            "".join(
                image(p, True, reviewed.get("current_verdicts", {}).get(p["photo_id"]))
                for p in r["baselines"]
            )
            or '<span class="muted">Не зафиксировано</span>'
        )
        cells = []
        for model in models:
            checked = reviewed.get("models", {}).get(model, {})
            out = lookup.get((rid, model))
            cell = '<span class="muted">Галерея ещё не получена</span>'
            urls = []
            status = "missing_gallery"
            if ready:
                status = "pending"
                cell = '<span class="muted">Ожидает прогона</span>'
            if out:
                status = out["status"]
                if status == "completed":
                    result = out["result"]
                    urls = result["cover"]
                    accepted = [p for p in result["results"] if p["url"] in urls]
                    checked = reviewed.get("models", {}).get(model, {})
                    cell = (
                        '<div class="photos">'
                        + "".join(
                            image(p, verdict=checked.get("verdicts", {}).get(p["photo_id"])) for p in accepted
                        )
                        + "</div>"
                    )
                    if not urls:
                        cell = '<span class="muted">Нет фото для автопубликации</span>'
                    cell += f"<small>{len(urls)} допущено · {len(out.get('model_selected_ids', []))} предложено</small>"
                    if checked:
                        labels = {
                            "better": "лучше",
                            "same": "примерно так же",
                            "worse": "хуже",
                            "uncertain": "неясно",
                        }
                        cell += f"<small>ИИ: {checked['score_before']:g} → {checked['score_after']:g} · {labels[checked['improvement']]}</small>"
                    details = "".join(
                        f"<li>{esc(p['photo_id'])}: {esc(p['decision'])} — {esc((p.get('assessment') or {}).get('reason', p.get('model_reason', p.get('reason_code', ''))))}</li>"
                        for p in result["results"]
                    )
                    cell += f"<details><summary>Решения</summary><ul>{details}</ul></details>"
                elif status == "not_run":
                    cell = '<span class="muted">Остановлено квотой / бюджетом</span>'
                else:
                    cell = '<span class="error">Нет валидного результата</span>'
            cells.append(f'<td class="after" data-model="{esc(model)}">{cell}</td>')
            csvrows.append(
                {
                    "restaurant_id": rid,
                    "restaurant": r["name"],
                    "source_url": r["url"],
                    "model": model,
                    "status": status,
                    "gallery_count": len(r["gallery"]),
                    "gallery_observed_metadata": r.get("gallery_source", {}).get(
                        "gallery_total_metadata", ""
                    ),
                    "source_metadata_complete": r.get("gallery_source", {}).get("metadata_complete", ""),
                    "ai_judge_score_before": checked.get("score_before", ""),
                    "ai_judge_score_after": checked.get("score_after", ""),
                    "ai_judge_improvement": checked.get("improvement", ""),
                    "loaded": r["gallery_inputs"],
                    "before_sprite_urls": " | ".join(p["url"] for p in r["baselines"]),
                    "before_crop_json": json.dumps(
                        [p.get("evidence") for p in r["baselines"]], ensure_ascii=False
                    ),
                    "after_urls": " | ".join(urls),
                    "reviewer_preference": "",
                    "reviewer_acceptable": "",
                    "reviewer": "",
                }
            )
        gallery = "".join(image(p) for p in r["gallery"])
        rows.append(
            f'<tr class="restaurant"><td class="identity"><a href="{esc(r["url"])}" target="_blank" rel="noopener"><b>{esc(r["name"])}</b></a><small>ID {esc(rid)} · {r["gallery_inputs"]}/{len(r["gallery"])} фото</small><details><summary>Галерея и ссылки</summary><div class="photos">{gallery or "Ожидает доступа к источнику"}</div></details></td><td class="before"><div class="photos">{before}</div><small>Фото карусели веб-карточки</small></td>{"".join(cells)}</tr>'
        )
    complete_common = set.intersection(
        *[
            {r["restaurant_id"] for r in results if r["model"] == m and r["status"] == "completed"}
            for m in models
        ]
    )
    summary = {
        "target": 50,
        "captured_cards": len(manifest["restaurants"]),
        "galleries_ready": sum(r["status"] == "ready" for r in manifest["restaurants"]),
        "gallery_photos_loaded": sum(r["gallery_inputs"] for r in manifest["restaurants"]),
        "common_completed": len(complete_common),
        "winner": None,
        "models": stats,
        "native_app_baseline": "not verified",
        "quality_note": "No independent human labels. AI judge findings are a provisional proxy, not production accuracy or a safety guarantee.",
    }
    summary["common_paid_completed"] = len(
        set.intersection(
            *[
                {r["restaurant_id"] for r in results if r["model"] == m and r["status"] == "completed"}
                for m in ["gpt-6-luna", "deepseek-flash"]
            ]
        )
    )
    summary["ai_audit"] = {k: v for k, v in audit.items() if k != "restaurants"}
    measured_gpt = next((m for m in stats if m["model"] == "gpt-6-luna"), {})
    summary["recommended_model"] = (
        "gpt-6-luna"
        if measured_gpt.get("completed_restaurants") == 50
        and audit["completed_paired_restaurants"] >= 30
        and audit["ai_preference_votes"].get("gpt-6-luna", 0)
        > audit["ai_preference_votes"].get("deepseek-flash", 0)
        else None
    )
    summary["recommendation_scope"] = (
        "Research pilot with verification/moderation; AI-judge preference is not human-validated production accuracy."
    )
    write_json(root / "summary.json", summary)
    heads = "".join(f'<th class="after" data-model="{esc(m)}">После · {esc(names[m])}</th>' for m in models)
    document = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Обложки ресторанов — до / после</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f5f3;color:#25272b;font:14px/1.45 system-ui,sans-serif}header{padding:22px 24px 16px;background:white;border-bottom:1px solid #ddd}h1{font-size:27px;margin:0 0 6px}p{margin:5px 0}.muted,small{color:#73767b}small{display:block;font-size:11px;margin-top:7px}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:14px}input,select{font:inherit;border:1px solid #ccc;border-radius:8px;padding:9px;background:white}a{color:inherit}header a{color:#3155a6}main{padding:0 20px 24px;overflow:auto}table{width:100%;border-collapse:collapse;table-layout:fixed;background:white}th{text-align:left;background:#eceff4;position:sticky;top:0;z-index:2;font-size:13px}th,td{padding:14px;border-bottom:1px solid #ddd;vertical-align:top}th:first-child{width:190px}th:nth-child(2){width:235px}.photos{display:flex;gap:7px;flex-wrap:wrap}.photo{display:block;width:100px;height:100px;position:relative;overflow:hidden;border-radius:7px;background:#eee;flex-shrink:0}.photo img{display:block;width:100%;height:100%;object-fit:cover}details{font-size:11px;margin-top:10px}details .photos{margin-top:10px}details ul{max-height:250px;overflow:auto;padding-left:15px}.badge{position:absolute;right:3px;top:3px;z-index:1;border-radius:10px;padding:0 6px;color:white;font-weight:700;font-size:13px;background:#ab7130}.badge.reject{background:#a83232}summary{cursor:pointer;color:#73767b}.error{color:#9b3929}.identity{overflow-wrap:anywhere}.before .photo{width:96px;height:96px}.hint{font-size:12px;color:#73767b}table{min-width:1050px}.single table{min-width:0}.single th:first-child{width:22%}.single th:nth-child(2){width:28%}.single td,.single th{padding:10px}.all-models table{min-width:2800px}.paired table{min-width:1000px}.single .after{width:auto}[hidden]{display:none!important}@media(max-width:700px){header{padding:16px}main{padding:0 8px}h1{font-size:22px}.photo,.before .photo{width:72px;height:72px}}
</style><header><h1>Обложки ресторанов: до / после</h1>"""
    document += f"<p>{summary['captured_cards']} карточек · {summary['galleries_ready']} тестовых пулов · {summary['gallery_photos_loaded']} фотографий · 30.10.2026</p>"
    document += '<p class="hint">До — кадры карусели веб-выдачи, восстановленные из её изображения и координат. После — фото, прошедшие модель и серверные правила. Native-приложение отдельно не проверялось.</p><div class="controls"><input id="filter" placeholder="Название ресторана" aria-label="Поиск"><select id="model" aria-label="Модель"><option value="all">Все модели рядом</option><option value="paid">GPT ↔ DeepSeek</option>'
    document += "".join(
        f'<option value="{esc(m)}" {"selected" if m == "gpt-6-luna" else ""}>{esc(names[m])}</option>'
        for m in models
    )
    document += (
        '</select><label><input type="checkbox" id="ready" checked> Только с галереей</label><a href="#conclusions">Итог по моделям ↓</a><a href="comparison.csv">CSV</a><a href="summary.json">Метрики</a><a href="analysis.json">ИИ-аудит</a><a href="manifest.json">Все источники</a><span class="hint">?/× — отметки ИИ-проверки</span></div></header><main><table><thead><tr><th>Ресторан</th><th>До · сейчас</th>'
        + heads
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></main>"
    )
    document += conclusions_table(summary, names)
    document += """<script>const filter=document.querySelector('#filter'),model=document.querySelector('#model'),ready=document.querySelector('#ready');function update(){document.body.classList.toggle('single',!['all','paid'].includes(model.value));document.body.classList.toggle('paired',model.value==='paid');document.body.classList.toggle('all-models',model.value==='all');document.querySelectorAll('[data-model]').forEach(e=>e.hidden=model.value==='paid'?!['gpt-6-luna','deepseek-flash'].includes(e.dataset.model):(model.value!=='all'&&e.dataset.model!==model.value));document.querySelectorAll('.restaurant').forEach(r=>r.hidden=!r.querySelector('.identity').textContent.toLowerCase().includes(filter.value.toLowerCase())||(ready.checked&&r.querySelector('.identity small').textContent.includes('0/0')))}filter.oninput=update;model.onchange=update;ready.onchange=update;update();</script></html>"""
    (root / "comparison.html").write_text(document)
    if csvrows:
        with (root / "comparison.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(csvrows[0]))
            writer.writeheader()
            writer.writerows(
                {
                    k: "'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v
                    for k, v in r.items()
                }
                for r in csvrows
            )
    return summary


def conclusions_table(summary, names):
    """Whole-study results remain visible when the restaurant table is filtered."""
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    audit = summary.get("ai_audit", {})
    judged = {r["model"]: r for r in audit.get("models", [])}
    votes = audit.get("ai_preference_votes", {})
    rows = []
    for index, row in enumerate(summary["models"]):
        model = row["model"]
        judge = judged.get(model)
        quality = (
            f"{judge['keep']}/{judge['photos']} ({100 * judge['ai_keep_fraction']:.1f}%)"
            if judge and judge.get("ai_keep_fraction") is not None
            else "не измерено"
        )
        if model == "gpt-6-luna":
            conclusion = "Основной кандидат; проверять спорные кадры"
        elif model == "deepseek-flash":
            conclusion = f"Быстрее; ошибок контракта: {row['failed_restaurants']}"
        elif row["completed_restaurants"]:
            conclusion = "Бесплатный пилот; малая выборка и ограничения квоты"
        else:
            conclusion = "Нет валидных результатов; качество не ранжируется"
        rank = str(index + 1) if row["completed_restaurants"] else "—"
        median = f"{row['median_call_seconds']:.1f}" if row.get("median_call_seconds") is not None else "—"
        values = [
            rank,
            names[model],
            f"{row['completed_restaurants']}/{row['calls']}",
            row["not_run_restaurants"],
            row["at_least_three"],
            median,
            f"${row['usage_estimated_upper_usd']:.6f}",
            quality,
            votes.get(model, "—"),
            conclusion,
        ]
        rows.append("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in values) + "</tr>")
    recommendation = (
        "GPT-6 Luna — основной облачный селектор; выбранные кадры проверять перед публикацией."
        if summary.get("recommended_model")
        else "Для этой выборки окончательная рекомендация пока не сформирована."
    )
    by_model = {row["model"]: row for row in summary["models"]}

    def completed(model):
        row = by_model.get(model, {})
        return str(row.get("completed_restaurants", 0)) + "/" + str(row.get("calls", 0))

    brief = (
        "GPT завершил "
        + completed("gpt-6-luna")
        + ", DeepSeek — "
        + completed("deepseek-flash")
        + ". Бесплатный Nemotron Super VL: "
        + completed("nvidia/nemotron-3.5-super-vl:free")
        + "; доступный объём меньше."
    )
    audit_note = f"ИИ-аудит: {audit.get('completed_paired_restaurants', 0)} общих успешных отборов; предпочтений GPT — {votes.get('gpt-6-luna', 0)}, DeepSeek — {votes.get('deepseek-flash', 0)}, ничьих — {votes.get('tie', 0)}, без подходящего нового набора — {votes.get('none', 0)}. Это оценка ИИ, не независимая человеческая точность. Доли keep относятся к принятым кадрам парной выборки, а не ко всем входам."
    heads = [
        "Порядок",
        "Модель",
        "Валидно / запросов",
        "Не запускалось",
        "Ресторанов с ≥3 обложками",
        "Медиана, с",
        "Расход прогона, USD",
        "Keep по ИИ-аудиту",
        "Предпочтений ИИ-судьи",
        "Вывод",
    ]
    return (
        '<style>#conclusions{margin:24px 20px 40px;padding:24px;background:#fff;border:1px solid #d5dae1;border-radius:12px;scroll-margin-top:12px}#conclusions h2{margin:0 0 10px}#conclusions .matrix{overflow:auto}#conclusions table{min-width:1100px;table-layout:auto;margin-top:18px}#conclusions th{width:auto}#conclusions td{padding:12px}#conclusions .verdict{font-size:18px;font-weight:600}#conclusions code{overflow-wrap:anywhere}</style><section id="conclusions"><h2>Итоговый вывод и сравнение моделей</h2><p class="verdict">'
        + esc(recommendation)
        + "</p><p>"
        + esc(brief)
        + '</p><div class="matrix"><table><thead><tr>'
        + "".join("<th>" + esc(h) + "</th>" for h in heads)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + '</tbody></table></div><p class="hint">'
        + esc(audit_note)
        + '</p><p class="hint">Стоимость — оценка по usage и верхним тарифам, без инфраструктуры и редактора. Бесплатные модели имеют меньший объём; незапущенные рестораны не считаются ошибками. Недоступные endpoints не ранжируются по визуальному качеству. Исходники фото открываются по ссылкам.</p></section>'
    )


if __name__ == "__main__":
    import sys

    print(json.dumps(render(sys.argv[1]), ensure_ascii=False))
