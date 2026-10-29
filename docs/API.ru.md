# API исследовательского стенда

`POST /api/select` — оценить URL, сохранить run, вернуть предложение обложки. Саму сохранённую обложку ресторана этот вызов не меняет.

```json
{
  "restaurant_id": "cremat-11-barcelona",
  "urls": ["https://img.example/food.jpg", "https://img.example/interior.jpg"],
  "config": {
    "provider": "openai",
    "model": "gpt-6-luna",
    "api_key": "YOUR_KEY",
    "input_price": 0.10,
    "output_price": 0.50
  },
  "policy": {
    "min_score": 75,
    "min_confidence": 0.85,
    "people_policy": "review_background",
    "max_covers": 6,
    "min_short_side": 480,
    "diversity": true
  }
}
```

`urls`: от 1 до 30, обычно 10; уникальные публичные HTTP(S) URL. Неподдерживаемые файлы, битые ответы и ошибки модели → `review`. Разрешение ниже порога и точные дубли → `reject` без обращения к модели. Для неизвестного формата не обещается распознавание его содержимого.

`config.provider`: `openrouter`, `huggingface`, `ollama`, `openai`, `deepseek`, `gemini`, `anthropic`, `compatible`, `technical`. Endpoint адреса задаются сервером, а не HTTP-клиентом. Ключ необязателен для Ollama/technical, для облака может браться из env. `input_price`/`output_price` — USD / 1M токенов; `null` означает неизвестную цену. В поле model API не проверяет наличие модели в аккаунте; недоступность будет ошибкой провайдера и review.

Ответ содержит `run_id`, `input_signature`, `restaurant_id`, `provider`, `model`, `prompt_version`, `policy`, `results[]`, `cover`, `forward_urls`, `rejected_urls`, `review_urls`, `shortfall`, `latency_ms`, `estimated_cost_usd`, `cost_complete`, `billing_note`, `semantic_model_used`. Ключи и сами base64-изображения в ответе не возвращаются. Поле `assessment.confidence` — самооценка модели, не калиброванная вероятность.

- `results[].decision=keep` означает пригодный кандидат. Их может быть больше шести.
- `cover` и `forward_urls` — выбранный набор максимум из шести.
- `review` не публикуется автоматически. При неполном ответе, refusal, неверном JSON и сетевой ошибке фото не пропускается дальше.
- `cost_complete=false` означает неполный расчёт, включая неизвестную стоимость неуспешных вызовов. Даже `true` не является подтверждением банковского списания.
- `GET /api/runs/{run_id}` — сохранённый результат конкретного прогона.
- `GET /api/providers` — presets и дата проверки цен.
- `GET /api/remote-models` — актуальные бесплатные vision/JSON модели OpenRouter; metadata GET без ключа и inference. Ошибка каталога → 503, текущие presets/runs сохраняют доступность.
- `GET /api/examples` — два готовых набора пользовательских ресторанов.
- `GET /api/health` — работоспособность самого сервиса; не обещает доступность выбранной модели.

## Сохранение и ручные изменения

`GET /api/restaurants/{restaurant_id}/cover`:

```json
{"restaurant_id":"cremat-11-barcelona","revision":0,"cover":[],"pinned":[],"excluded":[],"candidates":[],"known_urls":[],"run_id":null}
```

`PUT /api/restaurants/{restaurant_id}/cover`:

```json
{"run_id":"RUN_ID_FROM_SELECT","expected_revision":0}
```

Сохраняет автоматическую выборку, сохраняя действующие pins/exclusions. Run другого ресторана, technical-only и run с provider errors публиковать нельзя. После успешного запроса revision увеличивается.

`PATCH /api/restaurants/{restaurant_id}/cover`:

```json
{"url":"https://img.example/food.jpg","action":"pin","expected_revision":1}
```

`action`: `pin`, `exclude`, `reset`. Фото должно быть известно из сохранённого прогона этого ресторана. Pin — ручной override автоматического решения; оператор может закрепить отклонённый моделью кадр, поэтому это действие должно иметь редакторские права в production. Максимум шесть pins. Exclude удаляет из обложки и блокирует повторное автоматическое добавление. Reset убирает ручное правило; автоматическая пригодность берётся из последнего опубликованного run. Закреплённые кадры стоят первыми, оставшиеся места заполняются пригодными кандидатами.

`409` — конфликт revision; получить актуальное состояние и применить решение снова. `400` — несовместимая операция или нет ключа. `404` — неизвестный run. `422` — неверная схема. Валидация не отражает секретный input в ответе.

Это самостоятельный прототип API. Поле `cover` в боевой API Restaurant Guru не добавлено: исходников серверного проекта и его миграций в задаче не предоставлено. Предложенный production-контракт по photo ID находится в исследовании.

По умолчанию новые запросы используют `openrouter` / `google/gemma-4-31b-it:free`; нужен OpenRouter API-ключ. Модели исполняются у внешнего провайдера. `serving_provider` сохраняется, если он объявлен в ответе. Для OpenRouter отправляется один model ID, массив платных fallback не добавляется. `huggingface` использует `HF_TOKEN` и официальный router endpoint; стартовые кредиты не означают нулевую цену используемой модели.
