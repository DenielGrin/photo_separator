#!/usr/bin/env python3
"""Build an offline HTML research presentation from frozen benchmark evidence."""

import argparse
import base64
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/DenielGrin/photo_separator/blob/codex/photo-selection-research/"


def build(previews, output):
    dataset = json.loads((ROOT / "examples/labeled-pilot.json").read_text())
    photos = {}
    runs = []
    for path in (ROOT / "docs/benchmarks/free-cloud/952e7a96a37e7936").glob("*.json"):
        value = json.loads(path.read_text())
        if "results" in value:
            runs.append(value)
    decisions = {row["url"]: row for run in runs for row in run["results"]}
    for ri, restaurant in enumerate(dataset):
        for i, photo in enumerate(restaurant["photos"]):
            key = ("C" if ri == 0 else "R") + f"{i + 1:02}"
            row = decisions[photo["url"]]
            jpg = Path(previews) / (row["metrics"]["digest"] + ".jpg")
            if not jpg.is_file():
                raise SystemExit("Missing frozen preview: " + key)
            photos[key] = photo | {
                "image": "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode(),
                "source": restaurant["source_page"],
            }

    def picture(key, caption, cls=""):
        p = photos[key]
        return f'<figure class="{cls}"><a href="{html.escape(p["url"], quote=True)}" target="_blank" rel="noopener"><img src="{p["image"]}" alt="{html.escape(p["reason"], quote=True)}"></a><figcaption>{key} · {caption}</figcaption></figure>'

    def note(text, source="README.md"):
        return f'<aside class="notes">{text} <a href="{REPO + source}" target="_blank" rel="noopener">Источник и подробности</a></aside>'

    slides = []

    def add(title, content, cls="", notes=""):
        slides.append(
            f'<section class="slide {cls}" aria-label="{html.escape(title, quote=True)}"><div class="content">{content}</div>{notes}</section>'
        )

    add(
        "Обложки ресторанов: было → стало",
        """<p class="eyebrow">Restaurant Guru · серверное исследование · 29.10.2026</p><h1>Обложки<br>ресторанов</h1><p class="subtitle">Было → стало</p><p class="cover-line">От случайного кадра к проверяемому отбору фотографий</p><p class="muted">Результаты прототипа. В production изменения не внедрялись.</p>""",
        "dark cover",
    )
    add(
        "Проблема случайного фото",
        '<h2>Случайный кадр может испортить карточку</h2><div class="photos three">'
        + picture("C09", "Люди — главный объект")
        + picture("C08", "Текстовый постер")
        + picture("R06", "Тёмный нерезкий кадр")
        + '</div><p class="lead">Пользователь ждёт еду, интерьер и атмосферу ресторана.</p><p class="muted">Реальные фото выборки. Слайд иллюстрирует исходную проблему, а не частоту её появления в продукте.</p>',
        notes=note(
            "Проблема сформулирована пользователем. Эти кадры имеют предварительную метку reject; модельные решения могут отличаться."
        ),
    )
    add(
        "Было → стало: Райский сад",
        '<h2>«Райский сад»: случайный кадр → подборка Dots</h2><div class="before-after"><div><h3>Было: возможный случайный выбор</h3>'
        + picture("R10", "Селфи посетительниц")
        + '</div><div><h3>Стало: четыре выбранных кадра</h3><div class="photos two compact">'
        + "".join(
            picture(k, caption)
            for k, caption in [("R01", "Еда"), ("R04", "Интерьер"), ("R03", "Блюдо"), ("R02", "Хинкали")]
        )
        + '</div></div></div><p class="muted">«Было» — иллюстрация риска, не скриншот production. «Стало» — фактический cover сохранённого прогона Dots.</p>',
        notes=note(
            "Dots выбрала R01, R04, R03, R02. Все четыре совпадают с предварительной разметкой keep. Фото не заменены иллюстрациями и показаны с сохранением пропорций.",
            "docs/benchmarks/free-cloud/952e7a96a37e7936/raiskii-sad-balashikha.json",
        ),
    )
    add(
        "Серверный отбор по URL",
        """<h2>Отбор выполняется на сервере</h2><ol class="steps"><li><span>01</span><div><h3>Принимаем URL</h3><p>Загружаем изображения, проверяем размер и дубли.</p></div></li><li><span>02</span><div><h3>Отправляем фотографии в облачную модель</h3><p>Веса и GPU находятся у внешнего провайдера.</p></div></li><li><span>03</span><div><h3>Проверяем ответ и применяем правила</h3><p>Пригодные фото, отклонённые и требующие проверки получают разные статусы.</p></div></li><li><span>04</span><div><h3>Сохраняем подборку</h3><p>До шести URL в cover; редактор может закрепить или исключить фото.</p></div></li></ol>""",
        notes=note(
            "Показан реализованный прототип. Автоматический каскад двух моделей и production-интеграция не реализованы.",
            "docs/API.ru.md",
        ),
    )
    add(
        "Три решения для каждой фотографии",
        '<h2>Фото получает понятное решение</h2><div class="photos three decision">'
        + picture("C03", "Взять · напиток подходит")
        + picture("C01", "Проверить · человек на фоне")
        + picture("R08", "Отклонить · человек — главный объект")
        + '</div><p class="lead">Ошибка API тоже отправляет фото на проверку.</p><p class="muted">Фактические решения Dots. Кадры из review автоматически не попадают в cover.</p>',
        notes=note(
            "C03 keep, C01 review/background_people, R08 reject/unsuitable_subject. Ни число хороших фото, ни модельная confidence не гарантируют правильность решения.",
            "docs/FREE_RESULTS.ru.md",
        ),
    )
    add(
        "Что появилось в прототипе",
        """<h2>Что изменилось</h2><div class="text-compare"><div><h3>Исходная задача</h3><p>Случайная фотография в карточке.</p><p>Нет оценки пригодности кадра.</p><p>Нужны сохранённые обложки и ручной контроль.</p></div><div><h3>Реализованный прототип</h3><p>Оценка фотографий по URL и причины решения.</p><p>Сохранённое поле <code>cover</code> в API стенда.</p><p>Закрепление, исключение и история прогонов.</p><p>Переключение моделей и безопасное хранение результатов без API-ключей.</p></div></div><p class="muted">Действующий API Restaurant Guru и мобильные приложения не менялись.</p>""",
        notes=note(
            "Список подтверждён кодом серверного стенда. Это результат разработки прототипа, а не сравнение развернутых версий приложения."
        ),
    )
    table_rows = []
    for label, file in [
        ("Dots · free", "free-cloud/952e7a96a37e7936/metrics.json"),
        ("Nemotron Nano · free", "free-cloud/0c33da0574e6dd4e/metrics.json"),
        ("Qwen 2B · локально", "qwen2-metrics-v2.json"),
        ("Qwen 8B · локально", "qwen8-metrics-v2.json"),
        ("GPT-6 Luna · paid", "gpt-luna-metrics.json"),
        ("DeepSeek Flash · paid", "deepseek-metrics.json"),
    ]:
        m = json.loads((ROOT / "docs/benchmarks" / file).read_text())["metrics"]
        table_rows.append(
            f"<tr><td>{label}</td><td>{m['accept_precision']:.1%}</td><td>{m['good_recall']:.1%}</td><td>{m['decision_accuracy']:.0%}</td><td>{m['error_count']}/20</td></tr>"
        )
    add(
        "Результаты на одинаковых 20 фото",
        "<h2>Шесть моделей: полный набор из 20 фото</h2><table><thead><tr><th>Модель / размещение</th><th>Чистота принятых</th><th>Полнота</th><th>Accuracy</th><th>Сбои</th></tr></thead><tbody>"
        + "".join(table_rows)
        + '</tbody></table><p class="muted">Чистота = пригодные среди принятых; полнота = найденные из 8 хороших. Сбои учтены как review. Порядок строк не является общим рейтингом.</p><p class="muted">Разметка агентская: 8 keep / 8 reject / 4 review. Независимой проверки редакторами пока нет.</p>',
        notes=note(
            "Полные прогоны шести моделей используют одни исходные байты и разметку v2. GPT: 5/6 пригодных принятий; DeepSeek: 3/3, но мало обложек и четыре ошибки. Скорость локальных и облачных запусков не сравнивается."
        ),
    )
    add(
        "Проверка бесплатных облачных моделей",
        """<h2>Проверены все 8 бесплатных endpoints каталога</h2><div class="free-grid"><div><p class="big">48<span>/50</span></p><p>Бесплатных запросов засчитано.<br>Два оставлены в резерве.</p><p class="big money">$0</p><p>Денежный расход по данным OpenRouter.</p></div><div class="availability"><p><strong>Dots, Nemotron Nano</strong><br>Полный набор: по 20 фото.</p><p><strong>Nemotron Super VL</strong><br>Частичный набор: 13 фото.</p><p><strong>Gemma 26B / 31B</strong><br>HTTP 429 — качество не измерено.</p><p><strong>Inkling / Inkling Small</strong><br>HTTP 403 — качество не измерено.</p><p><strong>Content Safety</strong><br>Ответ не прошёл контракт оценки фото.</p></div></div>""",
        notes=note(
            "61 попытка зафиксирована до отправки; OpenRouter засчитал 48 дневных запросов. Не всякая попытка соответствует успешному запросу. Бесплатные модели не заменялись платными.",
            "docs/benchmarks/free-cloud/account-status.json",
        ),
    )
    add(
        "Результат Dots",
        """<h2>Dots: шесть пригодных фото из шести принятых</h2><div class="finding"><div class="huge">6<span>/6</span></div><div><p><strong>75%</strong> хороших кадров найдено</p><p><strong>3 из 20</strong> ответов оказались сбойными</p><p><strong>1 из 2</strong> ресторанов получил ≥3 обложки</p></div></div><p class="lead">Перспективно для пилота с редактором.</p><p class="muted">100% на шести принятиях не доказывают production-надёжность: нижняя граница Wilson 95% — около 61%.</p>""",
        "dark",
        note(
            "В Cremat Dots дала только C02/C03, без искусственного добора до трёх. Для Райского сада выбраны четыре фотографии. Сама confidence модели не является измеренной вероятностью правильности.",
            "docs/FREE_RESULTS.ru.md",
        ),
    )
    add(
        "Какую модель выбирать",
        """<h2>Мой выбор — Dots для управляемого пилота</h2><p class="lead">Облачное размещение, ни одного ошибочного принятия на нашей выборке и меньше сбоев, чем у Nano.</p><div class="conclusion"><p>Редактор подтверждает подборку до публикации.</p><p>Сбой, люди на фоне и сомнение остаются в review.</p><p>Если хороших фото меньше трёх — сохраняем недобор.</p></div><p class="muted">GPT завершил тест без технических ошибок, но принял один неподходящий кадр. Платная модель пока не дала лучшей чистоты.</p><p class="statement">Без редактора сейчас не рекомендую ни одну из проверенных моделей.</p>""",
        notes=note(
            "Это вывод по пилоту 29.10.2026 и текущему промпту, не универсальный рейтинг моделей. Dots исключена из строгого рейтинга без технических ошибок, но выбрана кандидатом для управляемого пилота."
        ),
    )
    add(
        "Следующий этап проверки",
        """<h2>Что нужно до автоматической публикации</h2><ol class="next"><li>Подтвердить метки независимыми редакторами.</li><li>Расширить корпус: другие рестораны, свет и сложные сцены.</li><li>Отдельно проверить меню и чеки — их нет в текущих 20 фото.</li><li>Завершить Super VL после восстановления квоты.</li><li>Заранее согласовать допустимые ошибки и долю ручной проверки.</li></ol><p class="muted">Проверка на большом отложенном корпусе и production-интеграция ещё впереди.</p>""",
        notes=note(
            "Это план следующих работ, не выполненные результаты. Размер текущего корпуса и целенаправленный отбор фотографий ограничивают переносимость метрик."
        ),
    )
    columns = []
    for ri, restaurant in enumerate(dataset):
        prefix = "C" if ri == 0 else "R"
        name = "Cremat 11 · Барселона" if ri == 0 else "Райский сад · Балашиха"
        links = "".join(
            f'<li><a href="{p["url"]}" target="_blank" rel="noopener">{prefix}{i + 1:02} · {html.escape(p["reason"].split(";")[0].split(". ")[0])}</a></li>'
            for i, p in enumerate(restaurant["photos"])
        )
        columns.append(
            f'<div><h3><a href="{restaurant["source_page"]}" target="_blank" rel="noopener">{name}</a></h3><ul class="source-list">{links}</ul></div>'
        )
    add(
        "Источники и все 20 фотографий",
        '<h2>Источники и все 20 фотографий</h2><div class="sources">'
        + "".join(columns)
        + f'</div><p class="muted"><a href="{REPO}README.md" target="_blank" rel="noopener">Подробный README</a> · <a href="{REPO}docs/FREE_RESULTS.ru.md" target="_blank" rel="noopener">Бесплатный тест</a> · <a href="{REPO}examples/labeled-pilot.json" target="_blank" rel="noopener">Manifest: URL, ID, SHA-256 и разметка</a></p>',
        notes=note(
            "Фото зафиксированы 29.10.2026; CDN может впоследствии изменить или удалить оригиналы. Встроенные изображения этой презентации взяты из сохранённых нормализованных превью, не генерировались ИИ. Внешние ссылки требуют интернета."
        ),
    )
    template = (ROOT / "scripts/presentation-template.html").read_text()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("<!-- SLIDES -->", "\n".join(slides)))
    print(
        f"{len(slides)} slides; {len(photos)} source photographs; {output.stat().st_size:,} bytes; {output}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previews", default=str(ROOT / "data/previews"))
    parser.add_argument("--output", default=str(ROOT / "results/presentation/photo-selection.html"))
    args = parser.parse_args()
    build(args.previews, args.output)
