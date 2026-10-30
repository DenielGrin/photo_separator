#!/usr/bin/env python3
"""Publish reproducible evidence and detailed Russian documentation; no keys or photo bytes in Git."""

import argparse
import json
import statistics
from pathlib import Path

from photo_separator.campaign import write_json
from scripts.real_cover_report import render


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--full-root", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    full = Path(args.full_root)
    summary = render(root)
    manifest = json.loads((root / "manifest.json").read_text())
    audit = summary["ai_audit"]
    destination = Path("docs/rostov/cover-panel-50-2026-10-30")
    destination.mkdir(parents=True, exist_ok=True)
    for name in [
        "source.json",
        "manifest.json",
        "summary.json",
        "analysis.json",
        "checked_proposals.json",
        "paid_protocol.json",
        "judge_protocol.json",
        "catalog-free.json",
        "budget.json",
        "judge_budget.json",
        "assistant-free-audit.json",
        "service-demos.json",
    ]:
        p = root / name
        if p.exists():
            write_json(destination / name, json.loads(p.read_text()))
    write_json(
        destination / "model-results.json",
        [json.loads(p.read_text()) for p in (root / "model_results").glob("*.json")],
    )
    allcalls = []
    for label, folder in [("full_gallery_v3", full), ("panel_v4", root)]:
        for kind in ["paid_calls", "judge_calls"]:
            for p in (folder / kind).glob("*.json"):
                c = json.loads(p.read_text())
                allcalls.append(c | {"study": label, "kind": kind, "call_id": p.stem})
    write_json(destination / "calls.json", allcalls)
    fullresults = [json.loads(p.read_text()) for p in (full / "model_results").glob("*.json")]
    write_json(
        destination / "full-gallery-v3-summary.json",
        {
            "restaurants_with_gallery": 18,
            "prepared_photos": sum(
                r["gallery_inputs"] for r in json.loads((full / "manifest.json").read_text())["restaurants"]
            ),
            "models": [
                {
                    "model": m,
                    "completed": sum(r["status"] == "completed" and r["model"] == m for r in fullresults),
                    "failed": sum(r["status"] == "failed" and r["model"] == m for r in fullresults),
                }
                for m in ["gpt-6-luna", "deepseek-flash"]
            ],
            "note": "Separate exploratory full-gallery protocol; never merged into the 50-restaurant sampled quality denominator.",
        },
    )
    modelcalls = {
        m["model"]: [
            c
            for c in allcalls
            if c["study"] == "panel_v4" and c["kind"] == "paid_calls" and c["model"] == m["model"]
        ]
        for m in summary["models"]
    }
    modelstats = {m["model"]: m for m in summary["models"]}
    judgestats = {m["model"]: m for m in audit["models"]}
    total = sum(c.get("accounted_usd", 0) for c in allcalls)
    source_table = []
    photo_lines = [
        "# Все URL изображений, использованных в основном тесте 50 ресторанов",
        "",
        "Дата: 30.10.2026. После — исходные URL выбранных фотографий; до — три кадра, воспроизведённые из одного изображения карусели. Одинаковые URL у трёх кадров до ожидаемы: различаются координаты `crop` в manifest.",
        "",
    ]
    for r in manifest["restaurants"]:
        gs = r["gallery_source"]
        complete = gs["metadata_complete"]
        observed = str(gs["gallery_total_metadata"]) + (" (префикс)" if not complete else "")
        source_table.append(
            f"| {r['listing_rank']} | [{r['name']}]({r['url']}) | `{r['restaurant_id']}` | {observed} | {r['gallery_inputs']} |"
        )
        photo_lines += [
            f"## {r['name']} · {r['restaurant_id']}",
            "",
            f"[Ресторан]({r['url']}) · метаданных фото: {observed} · входов теста: {r['gallery_inputs']}",
            "",
            "| Роль / ID | URL изображения | Исходная позиция в галерее |",
            "|---|---|---|",
        ]
        for p in r["baselines"]:
            photo_lines.append(
                f"| До · `{p['photo_id']}` | [Исходное изображение карусели]({p['url']}) | crop: `{json.dumps(next(iter(p['evidence'].values()))['crop'], ensure_ascii=False)}` |"
            )
        for p in r["gallery"]:
            photo_lines.append(
                f"| Тест · `{p['photo_id']}` | [Открыть фото]({p['url']}) | {p['source_metadata'].get('source_index_zero_based')} (с нуля) |"
            )
        photo_lines.append("")
    (destination / "PHOTO_URLS.ru.md").write_text("\n".join(photo_lines).rstrip() + "\n")
    rank = []
    for n, m in enumerate(summary["models"], 1):
        model = m["model"]
        calls = modelcalls[model]
        attempts = len(calls)
        median = statistics.median(c["seconds"] for c in calls) if calls else 0
        j = judgestats.get(model)
        quality = f"{j['keep']}/{j['photos']} ({100 * j['ai_keep_fraction']:.1f}%)" if j else "не оценено"
        position = str(n) if n <= 4 else "—"
        rank.append(
            f"| {position} | `{model}` | {m['completed_restaurants']}/{attempts} | {m['at_least_three']} | {median:.1f} с | ${m['usage_estimated_upper_usd']:.6f} | {quality} |"
        )
    partial = [r for r in manifest["restaurants"] if not r["gallery_source"]["metadata_complete"]]
    sources = "\n".join(source_table)
    ranking = "\n".join(rank)
    gpt = modelstats["gpt-6-luna"]
    deep = modelstats["deepseek-flash"]
    gq = judgestats["gpt-6-luna"]
    dq = judgestats["deepseek-flash"]
    votes = audit["ai_preference_votes"]
    prep = manifest["preparation"]
    storage = prep["cache_bytes"] + prep["asset_bytes"]
    report = f"""# Ростов: сравнение реальных обложек 50 ресторанов · 30.10.2026

**Рекомендация по завершённым тестам: `gpt-6-luna` как основной облачный селектор, с отдельной проверкой выбранных кадров и отправкой сомнительных на ручную модерацию.** Он завершил 50/50 запросов, оказался дешевле DeepSeek и чаще предпочитался слепым ИИ-судьёй. Это рекомендация для пилота; измеренной человеческой точности для production пока нет.

[Таблица до/после, локальный сервер](http://127.0.0.1:8879/comparison.html) · [Все фотографии и источники](rostov/cover-panel-50-2026-10-30/PHOTO_URLS.ru.md) · [Сводка](rostov/cover-panel-50-2026-10-30/summary.json) · [ИИ-аудит](rostov/cover-panel-50-2026-10-30/analysis.json) · [Полный журнал запросов](rostov/cover-panel-50-2026-10-30/calls.json)

## Что именно проверено

- 50 ранжированных карточек ресторанов из страниц 1–3 выдачи Ростова; одинаковое название сети не объединяет разные restaurant ID и адреса.
- 150 кадров текущих каруселей — по три на ресторан. На странице показаны отдельные фотографии карусели, а не весь JPEG-спрайт.
- {summary["gallery_photos_loaded"]} исходных тестовых фотографий: до 20 на ресторан, одинаковые URL и нормализованные байты для каждой модели.
- Две платные модели вызваны для всех 50 ресторанов. Все восемь бесплатных vision-endpoint текущего каталога OpenRouter тоже реально вызваны; доступность и квоты ограничили их объём.
- Отдельный глубокий тест полных галерей: 18 ресторанов и 1927 загруженных фото. GPT завершил 16/18, DeepSeek 14/18. Его результаты и затраты сохранены отдельно, в показатели общего теста 50 ресторанов не подмешаны.
- Слепой сравнительный ИИ-аудит GPT-6 Sol проведён на {audit["completed_paired_restaurants"]} ресторанах, где оба платных селектора вернули валидный результат. Все {audit["completed_paired_restaurants"]} проверки завершены. Остальные пять не превращены в проигрыши DeepSeek по эстетике: это отдельные ошибки протокола.

**Ограничение выборки:** тестовый пул до 20 фото не является всей галереей. Для полных метаданных взята одна позиция из каждого из 20 равных интервалов списка; выбор детерминирован seed `rostov-cover-panel-v4:<restaurant_id>`. При ≤20 фото взяты все. В двух больших галереях DOM-инструмент ограничил текст встроенных метаданных 200000 символами: {", ".join(r["name"] for r in partial)}. Для них выборка сделана по полностью разобранному доступному префиксу, а не по всей галерее. Точные индексы, полнота метаданных, исходные ID и URL записаны в manifest. Никакого заявления о просмотре всех фото всех 50 ресторанов нет.

## Модели от наиболее пригодной для этого стенда

Порядок первых четырёх учитывает надёжность протокола, доступный объём, стоимость и полученную оценку результата. Это не универсальный рейтинг интеллекта. После первых четырёх нет визуального основания ранжировать модели: они не дали пригодного ответа. HTTP 403/429/404 характеризует доступ endpoint с данным ключом, а не способность модели понимать фотографии.

| Порядок | Модель | Валидные рестораны / реальные запросы | Ресторанов с ≥3 обложками | Медиана запроса | Стоимость прогона, usage × верхний тариф | Кадры keep по ИИ-аудиту |
|---|---|---|---|---|---|---|
{ranking}

У бесплатных моделей остальные рестораны имеют статус `not_run`, а не «плохое фото» или ошибка качества. Nemotron Super VL: 4/5 валидных результатов; Dots: 1/5; Nano: 0/5. Gemma 4 26B/31B вернули HTTP 429, Inkling/Inkling Small — 403, content-safety — 404; у них по одному реальному запросу в основном тесте. Модель content-safety по назначению не является полноценным эстетическим селектором, но тоже проверена, поскольку присутствовала в каталоге с image input. Бесплатные маршруты фиксировались по каталогу с нулевой ценой; платного fallback нет.

Дополнительно ассистент просмотрел все 29 принятых кадров пяти успешных бесплатных отборов. У Super VL спорные кадры `R91344139-P001` и `R129957878-P013` с торсом/руками посетителя за едой; у Dots — очень тёмный `R129431563-P005`. Это замечания на review, а не независимая оценка accuracy. Поэтому лучший бесплатный endpoint по валидности не равен доказанно безопасному автоматическому селектору. Подробности: [assistant-free-audit.json](rostov/cover-panel-50-2026-10-30/assistant-free-audit.json).

### Что выбрал слепой ИИ-судья

Судья видел CURRENT и анонимные варианты A/B. Названия селекторов в его запрос не передавались. Раскладка A/B задаётся заранее чётностью SHA256 restaurant ID. Совпадающие фото передаются один раз, поэтому их вердикт одинаков для обеих моделей. Судья видит оригинал выбранного фото до 512 px и квадратное превью 224×224; текущая карусель показывается в её вырезанном кадре. Это сравнение квадратной обложки, не проверка всех возможных crops веба и native-приложения.

| Показатель на общих 45 ресторанах | GPT-6 Luna | DeepSeek Flash |
|---|---|---|
| Предпочтение A/B после раскрытия моделей | {votes.get("gpt-6-luna", 0)} | {votes.get("deepseek-flash", 0)} |
| «После» лучше текущей карусели | {gq["improvement_better"]}/45 | {dq["improvement_better"]}/45 |
| «После» хуже | {gq["improvement_worse"]}/45 | {dq["improvement_worse"]}/45 |
| Средний балл набора, 0–100 | {gq["mean_set_score"]} | {dq["mean_set_score"]} |
| Кадры keep | {gq["keep"]}/{gq["photos"]} | {dq["keep"]}/{dq["photos"]} |
| Кадры review | {gq["review"]} | {dq["review"]} |
| Кадры reject | {gq["reject"]} | {dq["reject"]} |

Ещё {votes.get("tie", 0)} ничья и {votes.get("none", 0)} случая без предпочтительного нового набора. Самооценки селекторов не использованы как доказательство улучшения.

**Это proxy-оценка ИИ, не независимая человеческая разметка и не production precision/recall.** Семейство судьи и GPT-селектора имеет одного провайдера; возможны вкусовая предвзятость и ошибки судьи. Оценивались принятые обложки, а не все отвергнутые фотографии, поэтому recall здесь не измерен. Только один запрос на ресторан/модель; устойчивость к повторным генерациям не измерена. Источники одного города и стратифицированный пул не доказывают перенос на все рестораны.

### Ошибки, которые нельзя скрывать ради красивого результата

Судья отметил у обеих платных моделей по два принятых кадра с отвлекающим меню/QR-стойкой: `R112529328-P020` (Guru) и `R104291258-P004` (Старик Хинкалыч). Эти два изображения дополнительно просмотрены ассистентом: крупная стойка с QR и печатная подложка действительно заметны; окончательное редакторское решение остаётся предметом модерации. В таблице они сохранены и помечены ×; спорные кадры — ?. Исследовательские результаты не исправлялись задним числом для повышения рейтинга.

`checked_proposals.json` содержит отдельный производный список: `verified_cover` только из keep, `review_urls` для review, `rejected_urls` для reject. Непроверенные кандидаты полностью отправляются в review. Это предложения после ИИ-проверки, а не опубликованные или одобренные человеком обложки. Меньше трёх keep допускается — плохие фото не добавляются ради количества.

## Что означает «До»

Веб-выдача отдаёт один JPEG, состоящий из нескольких фото, а показывает вырезанный кадр через CSS transform и clipping. Полный JPEG нельзя считать одной текущей обложкой. Восстановление использует DOM `data-collage-type`, `x1/x2/y1/y2`, margins, `first-id`, скрытый `pier-index` и aspect ratio; порядок и масштабирование сверены с публичным скриптом карточек. Не каждый слайд снят отдельным скриншотом; кадры карусели реконструированы по опубликованным координатам. Точное наблюдаемое активное окно сохранено в evidence. URL у трёх кадров может совпадать, координаты `crop` различаются.

Для lazy images служебный `log.gif` исключён; используется объявленный в DOM URL `data-src`. Предварительная ошибка с показом всего спрайта исправлена. Рекламная карточка Boemi меняла позицию/обложку и исключена из основного пула; её полный отдельный прогон сохранён. В основной выборке ровно 50 ranked restaurant IDs. Исходная поисковая ссылка пользователя возвращала нестабильную выдачу, поэтому использованы наблюдаемые ссылки официальной городской выдачи. Название города на странице одного результата — Aksay; карточка сохранена как реально присутствовавшая в выдаче, отдельный геофильтр не выдумывался.

[Исходный поиск пользователя](https://search.restaurantguru.com/rostov?location=ci1758&unikey=beda55ea526fcb41) · [Страница 1](https://restaurantguru.com/Rostov-on-Don) · [Страница 2](https://restaurantguru.com/Rostov-on-Don/2) · [Страница 3](https://restaurantguru.com/Rostov-on-Don/3)

Во время сбора сайт несколько раз требовал проверку человека; её проходил пользователь. Проверки не обходились. Текущие обложки нативного iOS/Android API не подтверждены: исследована веб-карусель на мобильной ширине и её изображения.

## Все 50 ресторанов и размер выборки

| Место в выдаче | Ресторан / источник | Restaurant ID | Доступных метаданных фото | Фото теста |
|---|---|---|---|---|
{sources}

**Все {summary["gallery_photos_loaded"]} URL кандидатов и 150 кадров «До» со своими координатами:** [PHOTO_URLS.ru.md](rostov/cover-panel-50-2026-10-30/PHOTO_URLS.ru.md). Сохраняются также SHA256 оригинала и нормализованного JPEG; URL сам по себе не гарантирует неизменность фотографии в будущем.

## Стоимость моделей и запросов

Тарифы проверены 30.10.2026. Цена зависит от image tokens, размеров входа, длины ответа и выбранного маршрута. Это не фиксированная цена «за фото».

| Модель | Input / 1M tokens | Output / 1M tokens | Измерено на 50 попытках, до 20 фото/ресторан | На ресторан | Сценарий 1000 ресторанов с таким же пулом |
|---|---|---|---|---|---|
| GPT-6 Luna | $0.10 | $0.50 | ${gpt["usage_estimated_upper_usd"]:.6f} | ${gpt["usage_estimated_upper_usd"] / 50:.6f} | ≈${gpt["usage_estimated_upper_usd"] * 20:.2f} |
| DeepSeek Flash, верхний тариф | $0.30 | $1.20 | ${deep["usage_estimated_upper_usd"]:.6f} | ${deep["usage_estimated_upper_usd"] / 50:.6f} | ≈${deep["usage_estimated_upper_usd"] * 20:.2f} |
| Бесплатные OpenRouter endpoints | $0 по проверенному каталогу | $0 | $0 за inference | квоты/доступ ограничены | 1000 за один день этим ключом недоступны |
| GPT-6 Sol, только ИИ-судья исследования | $2.00 | $10.00 | ${audit["judge_usage_cost_usd"]:.6f} за 45 сравнений | ≈${audit["judge_usage_cost_usd"] / 45:.6f} за сравнение CURRENT+A+B | не является ценой одиночного production-селектора |

Оценка рассчитана из фактического usage и верхних стандартных тарифов. Это не выписка из биллинга. DeepSeek off-peak стоит $0.15/$0.60; cached input тоже дешевле. Мы не заявляем точные скидки без подтверждения по счёту. В расходы включены запросы с полученным usage, даже если JSON не прошёл контракт. У неизвестного биллинга сохраняется консервативная резервная оценка.

**Все новые этапы этой исследовательской сессии вместе: ${total:.6f} по верхней оценке** (полные галереи v3 + основной panel v4 + слепой ИИ-аудит), ниже лимита $5. Это не включает расходы исторического пилота 29.10. Ограничитель резервирует консервативную сумму до dispatch и учитывает предыдущие журналы; повторно списываемые скрытые retry и платный fallback не используются.

Для 1000 ресторанов пример выше предполагает 20 тестовых фото на ресторан и тот же характер изображений/ответов. Это не цена 1000 произвольных HTTP-запросов и не цена полного сканирования всех галерей. У DeepSeek 45/50 валидных результатов: стоимость доведения до 1000 успешных отборов не измерена. Верификация выбранных 3–6 кадров, ручная работа и инфраструктура оплачиваются отдельно. Цена проверки только одного набора из шести кадров отдельно не измерялась; её формула — `$2 × input_tokens/1M + $10 × output_tokens/1M` для текущего GPT-6 Sol.

[OpenAI GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) · [OpenAI GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) · [DeepSeek: цены, native vision и peak hours](https://api-docs.deepseek.com/quick_start/pricing/) · [OpenRouter free limits](https://openrouter.ai/collections/free-models)

## Инфраструктура и работа модератора

Модели и GPU находятся у внешних провайдеров. На вашем устройстве/CPU-сервере остаются FastAPI, очередь, безопасная загрузка URL, Pillow-нормализация, хранилище результатов и HTML-отчёт. Ollama и скачивание весов не требуются.

| Вариант CPU-хостинга, EU | База/месяц | IPv4 | Backup, 20% базы | Итого до НДС |
|---|---|---|---|---|
| Hetzner CX23, cost-optimized, **в проверенном каталоге недоступен** | €5.49 | €0.50 | €1.10 | ≈€7.09 |
| Hetzner CPX22, более практичный ориентир | €19.49 | €0.50 | €3.90 | ≈€23.89 |

Это ориентиры, не арендованный сервер и не гарантия доступности конкретного региона/квоты. Курсовая конвертация USD/€ и налоги не подменены предположениями. Для пилота CPX22 — разумный бюджетный ориентир; окончательное sizing определяется нагрузочным тестом и retention.

В этой выборке originals/cache занимают {prep["cache_bytes"]:,} байт, нормализованные assets — {prep["asset_bytes"]:,} байт; вместе ≈{storage / 1e6:.1f} MB. Подготовка последнего прохода с тёплым кешем: {prep["wall_seconds"]} с wall и {prep["cpu_seconds"]} с CPU на машине разработки; новых сетевых загрузок в этом проходе {prep["new_downloads"]}. Это не холодный benchmark VPS. Простая линейная оценка одного поколения данных для 1000 похожих ресторанов — ≈{storage * 20 / 1e9:.2f} GB; это сценарий, не предел production-хранилища. Несколько версий требуют TTL/очистки или отдельного object storage. GPU в цену CPU-сервера не добавляется.

Ежемесячная смета: `CPU/IPv4/backup + API новых обработок + дополнительное хранилище/трафик сверх лимитов + работа редактора`. Повторно обрабатывать неизменные фото не нужно: использовать SHA256, версии промпта/политики и сохранённый cover. Работа модератора: `число review × среднее время × ставка`. Ставка и время вашей команды неизвестны; мы не выдаём выдуманную зарплату за измеренную стоимость. Двадцать review-кадров из 224 принятых GPT в парном ИИ-аудите показывают, почему труд редактора может стоить больше самих tokens.

[Hetzner: изменение цен](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) · [Серверы и availability](https://docs.hetzner.com/cloud/servers/overview/) · [Billing FAQ](https://docs.hetzner.com/cloud/billing/faq/)

## Как повторить и показать

```bash
uv sync --locked
# Основная таблица, без ключей и новых платных вызовов:
uv run python -m http.server 8879 --bind 127.0.0.1 --directory results/rostov-cover-panel-50-2026-10-30
# Импорт сохранённых демо в локальный API, без inference и публикации cover:
uv run python -m scripts.import_panel_demo --root results/rostov-cover-panel-50-2026-10-30
# API-стенд, по умолчанию без локальных моделей:
bash scripts/start-local.sh
```

После клонирования Git не содержит байты фотографий и приватные локальные результаты. Снимок source и все URL лежат в `docs/rostov/cover-panel-50-2026-10-30/`; нужен этап подготовки и импорт опубликованных наблюдений. Для существующей локальной машины всё уже сохранено в `results/`.

```bash
# Подготовить зафиксированные URL, не извлекать другую случайную выборку:
uv run python -m scripts.prepare_published_panel --source docs/rostov/cover-panel-50-2026-10-30/source.json --root results/reproduced-panel
# Новый ПЛАТНЫЙ прогон — отдельная папка без импорта прежних ответов:
uv run python -m scripts.prepare_published_panel --source docs/rostov/cover-panel-50-2026-10-30/source.json --root results/new-panel --without-observations
# Ключи запрашиваются скрыто:
uv run python -m scripts.run_authorized_campaign --root results/new-panel --providers openai deepseek --concrete-ids --max-cost-usd 5
# Отдельный слепой ИИ-аудит, при необходимости:
uv run python -m scripts.judge_cover_panel --root results/new-panel --max-cost-usd 5 --budget-root results/rostov-real-covers-2026-10-30
# Перестроить HTML/CSV из сохранённых наблюдений, без API:
uv run python -m scripts.real_cover_report results/rostov-cover-panel-50-2026-10-30
```

Для общего лимита нескольких выходных папок обязательно перечислять старые `--budget-root`; без этого лимит относится только к указанной кампании. Сырые дампы страниц и inline JS в Git не публикуются; photo metadata разбираются как данные и никогда не выполняются. Если CDN изменил изображение, сравнение должно фиксировать новый SHA256, а не считать его старым тестом.

`concrete-ids-selection-v4` передаёт реальные restaurant/photo IDs в примере JSON и отдельном списке разрешённых ID. Ответ обязан покрыть все входы ровно один раз. Чужие/пропущенные/повторённые ID и неверные поля не превращаются в частичный успешный cover. Выбранные фото получают полные оценки; остальные — reject/review/not_selected. `not_selected` не означает «плохое фото».

Серверные правила после модели: короткая сторона ≥480; prominent people/menu/receipt/unsafe/severe defect → reject; background people или confidence <0.85 → review; weighted score `0.40 quality + 0.35 appeal + 0.25 relevance` ≥75, quality/relevance ≥60. Максимум шесть; exact duplicates удаляются; предпочтение разнообразию категорий. Confidence не является калиброванной вероятностью. Если модель ошиблась в people/text, детерминированный gate эту ошибку сам не обнаружит — поэтому и нужен отдельный контроль.

## Итоговое решение

Использовать **GPT-6 Luna для основного облачного отбора**, хранить результат и обновлять только новые/изменившиеся фото. В пилоте публиковать после проверки предложений; review не добирать автоматически до трёх. DeepSeek — более быстрый альтернативный селектор, но на этом стенде он дороже по верхнему тарифу и имеет 5/50 ошибок контракта. Бесплатный Nemotron Super VL — лучший практически работающий бесплатный кандидат из этого прогона, с малым объёмом 4/5; квоты и задержки делают его пригодным для экспериментов и очереди, а не доказанным production SLA. Ни одна проверенная модель не дала основания обещать безошибочные обложки без контроля.
"""
    Path("docs/ROSTOV_RESULTS.ru.md").write_text(report)
    readme = Path("README.md")
    old = readme.read_text()
    marker = "<!-- rostov-v4:start -->"
    end = "<!-- rostov-v4:end -->"
    section = f"""{marker}
## Завершённый тест Ростова · 30.10.2026

**50 ресторанов · 150 кадров «До» · 996 фото галерей · 10 проверенных endpoints (8 бесплатных + GPT/DeepSeek).** [Открыть настоящую таблицу до/после](http://127.0.0.1:8879/comparison.html). На узком экране две колонки фотографий остаются рядом; есть переключение модели, парное сравнение GPT/DeepSeek, вся галерея и прямые ссылки на изображения.

**Мой вывод: основной кандидат — GPT-6 Luna.** Валидные результаты 50/50, против 45/50 у DeepSeek. Слепой GPT-6 Sol предпочёл GPT в 30 из 45 общих ресторанов, DeepSeek в 12; ещё одна ничья и два «нет предпочтительного нового набора». Это оценка ИИ, не человеческая точность. У GPT судья оставил 20 кадров на review и отклонил два: автоматическая публикация без контроля пока не обоснована.

Цена GPT по фактическому usage: **≈${gpt["usage_estimated_upper_usd"] / 50:.5f} за ресторан с пулом до 20 фото**, ориентир **≈${gpt["usage_estimated_upper_usd"] * 20:.2f} за 1000 таких ресторанов**. Для CPU-инфраструктуры ориентир CPX22+IPv4+backup **≈€23.89/мес до НДС**; модерация и верификация отдельно. Новые этапы исследования вместе ≈${total:.2f} по верхним тарифам, при лимите $5. Ключи использовались в памяти процессов и в файлы/Git не сохранялись.

### Сравнение от наиболее пригодной для стенда

| Порядок | Модель | Валидные рестораны / запросы | ≥3 обложки | Медиана | Расход прогона | Keep по ИИ-аудиту |
|---|---|---|---|---|---|---|
{ranking}

Бесплатные модели имеют меньший объём из-за квот и доступа; 45/49 незапущенных ресторанов у них не считаются неудачами. После первых четырёх нет оснований ранжировать визуальное качество: endpoint не дал пригодного ответа. Самый рабочий бесплатный вариант этого прогона — **Nemotron Super VL**, но 4/5 недостаточно для production-гарантий.

### Источники и подробности

Исходный поиск: [Restaurant Guru / Rostov](https://search.restaurantguru.com/rostov?location=ci1758&unikey=beda55ea526fcb41). Фактическая выборка собрана из [страницы 1](https://restaurantguru.com/Rostov-on-Don), [2](https://restaurantguru.com/Rostov-on-Don/2), [3](https://restaurantguru.com/Rostov-on-Don/3). «До» — отдельные кадры реальной веб-карусели, реконструированные по DOM/CSS, а не весь составной JPEG. Нативные мобильные обложки отдельно не проверялись. Тестируется фиксированный пул до 20 фото на ресторан; в двух галереях наблюдаемы только префиксы метаданных. Полный скан всех галерей 50 ресторанов не заявляется.

| Место | Ресторан / ссылка | ID | Метаданных фото | Входов теста |
|---|---|---|---|---|
{sources}

- [Полный отчёт: методика, ошибки, цены запросов/инфраструктуры, ограничения и воспроизведение](docs/ROSTOV_RESULTS.ru.md).
- [Все 996 URL фотографий и 150 кадров «До» с координатами](docs/rostov/cover-panel-50-2026-10-30/PHOTO_URLS.ru.md).
- [Сводка и слепой аудит](docs/rostov/cover-panel-50-2026-10-30/summary.json), [полный журнал запросов с usage](docs/rostov/cover-panel-50-2026-10-30/calls.json).
- [Предложения после ИИ-проверки: keep/review/reject](docs/rostov/cover-panel-50-2026-10-30/checked_proposals.json).
- [Сервис: сохранённый разбор 10 фото Накатильни, без API-ключа](http://127.0.0.1:8765/?run=18db9e5c2634e207876187c453e095b0).
- [Десять URL для демонстрации](docs/rostov/cover-panel-50-2026-10-30/demo-urls.txt) — один ресторан, еда/напитки, меню и портреты.
- [Комментарий для задачи](docs/TASK_COMMENT.ru.md).

Ниже сохранены исторические результаты пилота 29.10 на двух ресторанах. Они имеют другой протокол и не подменяют новые 50 ресторанов.
{end}
"""
    if marker in old:
        a = old.index(marker)
        b = old.index(end, a) + len(end)
        old = old[:a] + section.rstrip() + "\n\n" + old[b:].lstrip("\n")
    else:
        title, body = old.split("\n", 1)
        old = title + "\n\n" + section.rstrip() + "\n\n" + body.lstrip("\n")
    old = old.replace(
        "[Итоговая рекомендация по модели](#мой-вывод-какую-модель-использовать)",
        "[Актуальная рекомендация по модели](docs/ROSTOV_RESULTS.ru.md#итоговое-решение)",
    )
    historical_heading = "## Мой вывод: какую модель использовать"
    historical_note = "\n\n> Исторический вывод пилота 29.10.2026. Актуальное решение по 50 ресторанам: [GPT-6 Luna](docs/ROSTOV_RESULTS.ru.md#итоговое-решение).\n"
    if historical_heading in old and historical_heading + historical_note not in old:
        old = old.replace(historical_heading, historical_heading + historical_note)
    old = "\n".join(
        line for line in old.split("\n") if not line.startswith("> **Новое исследование, 30.10.2026:**")
    )
    readme.write_text(old)
    study = Path("docs/ROSTOV_STUDY.ru.md")
    text = study.read_text()
    banner = "> **Обновление:** основной тест 50 ресторанов завершён. [Итоговые результаты, таблица моделей и все источники](ROSTOV_RESULTS.ru.md). Ниже — историческое планирование и smoke-тесты до восстановления доступа; их статусы не являются текущим итогом.\n\n"
    if not text.startswith("> **Обновление:**"):
        study.write_text(banner + text)
    Path("docs/TASK_COMMENT.ru.md").write_text(
        "30.10.2026\n\n- Исследование восьми бесплатных vision-моделей и платных GPT / DeepSeek\n- Сравнение обложек 50 ресторанов Ростова: 150 текущих кадров и 996 тестовых фото\n- Таблица «До / После», слепой ИИ-аудит и расчёт стоимости API / инфраструктуры\n- Подробный README с источниками фотографий и рекомендацией GPT-6 Luna\n"
    )
    print(
        json.dumps(
            {
                "published": str(destination),
                "research_cost_upper_usd": total,
                "judged": audit["completed_paired_restaurants"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
