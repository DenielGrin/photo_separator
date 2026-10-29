import asyncio
import json

import httpx
import pytest

from photo_separator import selection
from photo_separator.catalog import free_vision_models
from photo_separator.images import prepare
from photo_separator.providers import ProviderResult, assess
from photo_separator.schemas import ProviderConfig, SelectionRequest
from scripts.benchmark_free import Journal, PauseRun, signature
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
