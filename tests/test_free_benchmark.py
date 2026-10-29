import asyncio
import json

import httpx
import pytest

from photo_separator import selection
from photo_separator.catalog import free_vision_models
from photo_separator.images import prepare
from photo_separator.providers import ProviderResult, assess
from photo_separator.schemas import ProviderConfig, SelectionRequest
from scripts.benchmark_free import Journal, ModelUnavailable, PauseRun, invocation_budget, signature
from scripts.update_leaderboard import ranked
from tests.test_catalog import candidate
from tests.test_images import png
from tests.test_selection import assessment


def test_full_catalog_accepts_prompt_only_but_never_paid():
    rows = free_vision_models(
        {
            "data": [
                candidate(supported_parameters=[]),
                candidate(id="paid:free", pricing={"prompt": "0", "completion": "0", "request": ".001"}),
            ]
        },
        require_json=False,
    )
    assert len(rows) == 1 and rows[0]["json_mode"] == "prompt"


def test_openrouter_prompt_mode_omits_unsupported_parameter_and_no_fallback():
    def handler(request):
        body = json.loads(request.content)
        assert "response_format" not in body and "models" not in body
        assert body["model"] == "test/model:free"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(assessment().model_dump())}}
                ],
                "usage": {},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await assess(
                ProviderConfig(
                    provider="openrouter", api_key="test-only", model="test/model:free", json_mode="prompt"
                ),
                prepare(png()),
                client,
            )

    assert asyncio.run(run()).assessment.category == "interior"


def test_budget_checkpoint_and_resume_does_not_repeat_success(monkeypatch, tmp_path):
    calls = []

    async def fake(config, image, client):
        calls.append(image.digest)
        return ProviderResult(assessment(), {"input_tokens": 1, "output_tokens": 1}, "fake")

    monkeypatch.setattr(selection, "assess", fake)
    urls = ["https://example.com/a", "https://example.com/b"]
    request = SelectionRequest(
        restaurant_id="r", urls=urls, config=ProviderConfig(provider="ollama", input_price=0, output_price=0)
    )

    def loader(url):
        return png(color="red" if url.endswith("a") else "blue")

    path = tmp_path / "checkpoint.json"
    journal = Journal(path, "frozen", 1)
    with pytest.raises(PauseRun):
        asyncio.run(
            selection.select(
                request, loader=loader, concurrency=1, before_assess=journal.before, on_assessed=journal.after
            )
        )
    assert len(calls) == 1 and len(journal.state["rows"]) == 1
    resumed = Journal(path, "frozen", 1)
    result = asyncio.run(
        selection.select(
            request,
            loader=loader,
            concurrency=1,
            before_assess=resumed.before,
            on_assessed=resumed.after,
            cached_results=resumed.state["rows"],
        )
    )
    assert len(calls) == 2 and sum(r.get("cache_hit", False) for r in result["results"]) == 1
    with pytest.raises(ValueError):
        Journal(path, "changed", 1)


def test_signature_freezes_prompt_mode_and_model():
    a = {"model": "a:free", "json_mode": "object"}
    assert signature(b"dataset", a) != signature(b"new-dataset", a)
    assert signature(b"dataset", a) != signature(b"dataset", a | {"json_mode": "prompt"})


def test_quota_failure_checkpoint_pauses(tmp_path):
    journal = Journal(tmp_path / "c.json", "a", 10)
    with pytest.raises(PauseRun):
        journal.after({"url": "u", "error": "Provider HTTP 429; quota", "assessment": None})
    assert json.loads(journal.path.read_text())["rows"]["u"]["assessment"] is None


def test_ranking_excludes_reject_all_errors_and_changed_dataset():
    def row(model, purity, **changes):
        return {
            "model": model,
            "dataset_sha256": "same",
            "metrics": {
                "photos": 20,
                "error_count": 0,
                "accepted": 8,
                "good_recall": 0.8,
                "cover_precision": purity,
                "decision_accuracy": 0.9,
                "ndcg_at_6": 0.9,
            }
            | changes,
        }

    reports = [
        row("b", 0.8),
        row("a", 0.9),
        row("reject-all", 1, accepted=0),
        row("broken", 1, error_count=1),
        row("wrong-data", 1) | {"dataset_sha256": "other"},
    ]
    winners, excluded = ranked(reports, "same")
    assert [r["model"] for r in winners] == ["a", "b"] and len(excluded) == 3


def test_budget_reserves_two_and_respects_user_cap():
    assert invocation_budget(160, 50) == 48
    assert invocation_budget(48, 49) == 47
    assert invocation_budget(40, 500) == 40
    assert invocation_budget(40, 1) == 0
    assert invocation_budget(160, None) == 40


def test_429_is_model_unavailability_not_bad_vision(tmp_path):
    journal = Journal(tmp_path / "c.json", "a", 10)
    with pytest.raises(ModelUnavailable):
        journal.after({"url": "u", "error": "Provider HTTP 429", "assessment": None})
    with pytest.raises(PauseRun) as error:
        journal.after({"url": "u", "error": "Provider HTTP 401", "assessment": None})
    assert not isinstance(error.value, ModelUnavailable)


def test_suite_probes_all_models_before_full_run_and_never_exceeds_budget(monkeypatch, tmp_path):
    import hashlib
    from argparse import Namespace

    from scripts import benchmark_free

    urls = [f"https://example.com/{i}" for i in range(20)]
    dataset = [
        {
            "restaurant_id": str(j),
            "photos": [{"url": url, "expected": "keep", "grade": 3} for url in urls[j * 10 : (j + 1) * 10]],
        }
        for j in range(2)
    ]
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset))
    cache = tmp_path / "cache"
    cache.mkdir()
    for i, url in enumerate(urls):
        (cache / (hashlib.sha256(url.encode()).hexdigest() + ".bin")).write_bytes(
            png(color=(i * 10, 50, 100))
        )
    candidates = [
        {
            "provider": "openrouter",
            "model": f"{name}:free",
            "input_price": 0,
            "output_price": 0,
            "json_mode": "object",
        }
        for name in ("a", "b")
    ]

    async def discover(**kwargs):
        return candidates

    calls = []

    async def fake_assess(config, image, client):
        calls.append(config.model)
        return ProviderResult(assessment(), {"input_tokens": 1, "output_tokens": 1}, config.model)

    real_client = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/key"
        return httpx.Response(200, json={"data": {"free_model_daily_requests": {"remaining": 50}}})

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setattr(benchmark_free, "discover_free_vision", discover)
    monkeypatch.setattr(selection, "assess", fake_assess)
    monkeypatch.setattr(benchmark_free.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(benchmark_free.time, "sleep", lambda _: None)
    output = tmp_path / "output"
    asyncio.run(
        benchmark_free.main(
            Namespace(
                output=str(output), dataset=str(dataset_path), cache=str(cache), max_calls=8, plan_only=False
            )
        )
    )
    assert calls[:2] == ["a:free", "b:free"] and len(calls) == 8
    plan = json.loads((output / "plan.json").read_text())
    assert plan["effective_max_calls"] == 8 and plan["status"] == "invocation_budget_exhausted"


def test_total_timeout_is_fail_closed_and_does_not_publish(monkeypatch):
    async def deadline(awaitable, timeout):
        assert timeout == 150
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(selection.asyncio, "wait_for", deadline)
    request = SelectionRequest(
        restaurant_id="r", urls=["https://example.com/a"], config=ProviderConfig(provider="ollama")
    )
    result = asyncio.run(selection.select(request, loader=lambda _: png()))
    assert result["cover"] == []
    assert result["results"][0]["reason_code"] == "provider_error"
    assert "150 seconds" in result["results"][0]["error"]


def test_benchmark_can_preserve_failed_observations_without_retry(monkeypatch):
    async def must_not_call(*args):
        raise AssertionError("Previously observed failure must not be retried")

    monkeypatch.setattr(selection, "assess", must_not_call)
    url = "https://example.com/a"
    request = SelectionRequest(restaurant_id="r", urls=[url], config=ProviderConfig(provider="ollama"))
    row = {
        "url": url,
        "index": 0,
        "metrics": prepare(png()).metrics(),
        "assessment": None,
        "decision": "review",
        "reason_code": "provider_error",
        "error": "Original incomplete response",
        "usage": {},
        "cost_estimate_usd": None,
    }
    result = asyncio.run(
        selection.select(request, loader=lambda _: png(), cached_results={url: row}, reuse_failures=True)
    )
    assert result["results"][0]["error"] == row["error"] and result["results"][0]["cache_hit"]
    assert result["cover"] == []
