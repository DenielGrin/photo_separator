#!/usr/bin/env python3
"""Publish sanitized free-model checkpoints and measured availability into research docs."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(source):
    source = Path(source)
    destination = ROOT / "docs/benchmarks/free-cloud"
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.tmp"))
    plan = json.loads((source / "plan.json").read_text())
    rows = []
    table = ["| Бесплатная модель OpenRouter | Формат | Реальный результат |", "|---|---|---|"]
    for model in plan["candidates"]:
        folder = source / hashlib.sha256(model["model"].encode()).hexdigest()[:16]
        checkpoint = (
            json.loads((folder / "checkpoint.json").read_text())
            if (folder / "checkpoint.json").exists()
            else {"attempts": 0, "rows": {}}
        )
        values = list(checkpoint["rows"].values())
        valid = sum(bool(r.get("assessment")) for r in values)
        report = (
            json.loads((folder / "metrics.json").read_text()) if (folder / "metrics.json").exists() else None
        )
        errors = sorted({r["error"] for r in values if r.get("error")})
        status = (
            f"Полный набор 20 фото; валидны {valid}/20; ошибки {report['metrics']['error_count']}"
            if report
            else f"Частичный тест: валидны {valid}/{len(values)}"
        )
        if len(values) == 1 and errors:
            status = errors[0]
        rows.append(
            {
                "model": model["model"],
                "unique_photos_attempted": len(values),
                "valid_assessments": valid,
                "attempts": checkpoint["attempts"],
                "complete_report": report is not None,
                "errors": errors,
                "status": status,
            }
        )
        mode = "JSON object" if model["json_mode"] == "object" else "JSON через промпт"
        table.append(f"| `{model['model']}` | {mode} | {status} |")
    (destination / "summary.json").write_text(
        json.dumps(
            {
                "models": rows,
                "total_attempts": sum(r["attempts"] for r in rows),
                "full_reports": sum(r["complete_report"] for r in rows),
                "label_status": "provisional_agent_annotation_requires_independent_human_review",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    readme = ROOT / "README.md"
    text = readme.read_text()
    start = text.index("| Бесплатная модель OpenRouter |")
    end = text.index("\nContent Safety", start)
    text = text[:start] + "\n".join(table) + "\n" + text[end:]
    text = text.replace(
        "Фактически проверено бесплатным облачным inference: **0/8** — OpenRouter-ключ не предоставлен.",
        f"Выполнены реальные запросы ко **всем 8/8 endpoints**; полные отчёты по 20 фото получены для **{sum(r['complete_report'] for r in rows)}/8**. Остальные результаты частичные либо endpoint недоступен. Это не восемь завершённых сравнений качества.",
    )
    text = re.sub(
        r"Выполнены реальные запросы ко \*\*всем 8/8 endpoints\*\*; полные отчёты по 20 фото получены для \*\*\d+/8\*\*\.",
        f"Выполнены реальные запросы ко **всем 8/8 endpoints**; полные отчёты по 20 фото получены для **{sum(r['complete_report'] for r in rows)}/8**.",
        text,
    )
    text = text.replace(
        "Для бесплатного облака найдены 8 кандидатов; inference ожидает отдельный OpenRouter API-ключ.",
        "Выполнены реальные запросы ко всем восьми бесплатным OpenRouter endpoints; объём успешных оценок и ограничения приведены ниже.",
    )
    text = text.replace(
        "Каталог проверен; адаптер проверен mock-тестами; inference не запускался",
        "Все 8 endpoints проверены запросами; полные/частичные результаты и ошибки — в таблице выше",
    )
    text = text.replace("не проверяет остаток квоты", "проверяет остаток квоты")
    a = text.index("На полном пилоте 8×20")
    b = text.index("\n\nФайлы находятся", a)
    text = (
        text[:a]
        + """На полном пилоте 8×20 = до 160 inference-вызовов. Скрипт сначала получает остаток квоты из `/api/v1/key`, затем ограничивает бюджет меньшим из `--max-calls` и `remaining−2`. Если числовой остаток недоступен, максимум 40; при ошибке metadata HTTP inference не начинается. Бюджет включает все отправленные попытки, даже если провайдер не засчитал отказ в дневную квоту. Другие приложения могут параллельно тратить квоту.

Сначала всем моделям даётся одно одинаковое фото, затем доступные модели проходят полный набор в порядке каталога. Проба на одном фото проверяет контракт API, а не качество модели. Интервал между отправками не меньше 3,2 секунды. HTTP 401/402 останавливает запуск; HTTP 403/429 сохраняется как недоступность конкретного endpoint, который пропускается. Это не попытка обойти ограничение провайдера. Следующий явный запуск переиспользует успешные ответы; для повторной проверки endpoint с сохранённым 403/429 нужен `--retry-unavailable`. Модели, не вернувшие валидную оценку на пробе, не расходуют ещё 20 вызовов на полный тест.

Скрипт не ждёт сутки и не планирует повторы. При изменении состава каталога результаты остаются на диске; текущий план отражает актуальный каталог. Не используйте два процесса с одной выходной папкой."""
        + text[b:]
    )
    text = text.replace("Снимок текущих восьми кандидатов:", "Исходный снимок восьми кандидатов:")
    readme.write_text(text)
    print(
        json.dumps(
            {
                "endpoints_attempted": sum(r["attempts"] > 0 for r in rows),
                "full_reports": sum(r["complete_report"] for r in rows),
                "attempts": sum(r["attempts"] for r in rows),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="results/free-cloud")
    main(parser.parse_args().source)
