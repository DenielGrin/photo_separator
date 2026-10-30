import asyncio
import hashlib
import json

import httpx
import pytest

from photo_separator import campaign
from photo_separator.images import prepare
from scripts.rostov_campaign import render_report
from tests.test_images import png
from tests.test_selection import assessment


def restaurant(rid="1", count=2):
    return {
        "restaurant_id": rid,
        "name": "Test",
        "url": "https://example.com/r/" + rid,
        "status": "ready",
        "baselines": [],
        "gallery": [
            {
                "photo_id": f"R{rid}-P{i + 1:03}",
                "asset": "assets/" + "a" * 64 + ".jpg",
                "url": f"https://example.com/{rid}/{i}",
                "metrics": {"width": 900, "height": 800, "digest": f"{rid}-{i}"},
            }
            for i in range(count)
        ],
    }


def payload(restaurants):
    return json.dumps(
        {
            "restaurants": [
                {
                    "restaurant_id": r["restaurant_id"],
                    "selected": [
                        assessment().model_dump() | {"photo_id": p["photo_id"]}
                        for p in r["gallery"][:6]
                        if p.get("asset")
                    ],
                    "remainder": [
                        {
                            "photo_id": p["photo_id"],
                            "disposition": "not_selected",
                            "reason": "Outside top six",
                        }
                        for p in r["gallery"][6:]
                    ],
                }
                for r in restaurants
            ]
        }
    )


def test_batches_never_truncate_and_preserve_complete_restaurants():
    rows = [restaurant(str(i), 20) for i in range(5)]
    groups = campaign.batch_plan(rows, max_images=44, max_restaurants=2)
    assert [len(g) for g in groups] == [2, 2, 1]
    assert sum(len(r["gallery"]) for g in groups for r in g) == 100
    with pytest.raises(ValueError):
        campaign.batch_plan([restaurant(count=45)])


def test_contract_rejects_missing_foreign_and_duplicate_photo_ids():
    rows = [restaurant("1"), restaurant("2")]
    assert set(campaign.parse_reply(payload(rows), rows)) == {"1", "2"}
    for replacement in ("R1-P900", "R1-P001"):
        bad = json.loads(payload(rows))
        bad["restaurants"][0]["selected"][1]["photo_id"] = replacement
        with pytest.raises(ValueError):
            campaign.parse_reply(json.dumps(bad), rows)
    bad = json.loads(payload(rows))
    bad["restaurants"][1]["restaurant_id"] = "unknown"
    with pytest.raises(ValueError):
        campaign.parse_reply(json.dumps(bad), rows)
    bad = json.loads(payload(rows))
    bad["restaurants"].pop()
    with pytest.raises(ValueError):
        campaign.parse_reply(json.dumps(bad), rows)


def test_baselines_are_diagnostic_and_never_selected_as_new_cover():
    r = restaurant()
    r["baselines"] = [r["gallery"][0] | {"photo_id": "R1-B01", "url": "https://example.com/old"}]
    result = campaign.restaurant_result(r, campaign.parse_reply(payload([r]), [r])["1"])
    assert len(result["cover"]) == 2 and "https://example.com/old" not in result["cover"]
    assert result["baseline_diagnostics"] == []


def test_prepare_keeps_missing_sources_and_sha256_without_faking_success(monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, "download", lambda _: png())
    source = {
        "capture_date": "2026-10-30",
        "restaurants": [
            {
                "restaurant_id": "1",
                "name": "=formula",
                "url": "https://example.com/r1",
                "gallery": [{"url": "https://example.com/a", "photo_id": "original"}],
                "baselines": {
                    "desktop_web": {"declared_url": "https://example.com/b"},
                    "mobile_web": {"declared_url": "https://example.com/b"},
                },
            },
            {"restaurant_id": "2", "name": "Missing", "url": "https://example.com/r2", "baselines": {}},
        ],
    }
    frozen = campaign.prepare_campaign(source, tmp_path, 50)
    assert len(frozen["restaurants"]) == 2 and frozen["restaurants"][1]["status"] == "missing_gallery"
    assert len(frozen["restaurants"][0]["baselines"]) == 1
    assert frozen["restaurants"][0]["baselines"][0]["surfaces"] == ["desktop_web", "mobile_web"]
    assert frozen["restaurants"][0]["gallery"][0]["sha256"] == hashlib.sha256(png()).hexdigest()
    summary = render_report(frozen, tmp_path, ["test:free"])
    assert summary["winner"] is None and summary["common_completed_restaurants"] == 0
    assert "quality_precision" in summary["models"][0] and summary["models"][0]["quality_precision"] is None
    assert "'=formula" in (tmp_path / "comparison.csv").read_text()
    assert "Результата модели пока нет" in (tmp_path / "comparison.html").read_text()


def test_paid_model_is_refused_before_any_inference(monkeypatch, tmp_path):
    async def catalog(**kwargs):
        return [{"model": "test:free"}]

    monkeypatch.setattr(campaign, "discover_free_vision", catalog)
    frozen = {"protocol": campaign.VERSION, "restaurants": []}
    frozen["manifest_sha256"] = campaign.fingerprint(frozen)
    with pytest.raises(ValueError, match="zero-price"):
        asyncio.run(campaign.run_campaign(frozen, tmp_path, ["paid/model"], "test-key"))


@pytest.mark.parametrize("reported_cost", [0, 0.01])
def test_quota_guard_and_payload_integrity_with_exact_checkpoint_reuse(monkeypatch, tmp_path, reported_cost):
    image = prepare(png())
    data = image.jpeg
    asset = "assets/" + image.digest + ".jpg"
    (tmp_path / "assets").mkdir()
    (tmp_path / asset).write_bytes(data)
    r = restaurant("1", 1)
    r["gallery"][0].update(
        asset=asset, input_sha256=hashlib.sha256(data).hexdigest(), metrics=image.metrics()
    )
    frozen = {"restaurants": [r], "protocol": campaign.VERSION, "target_restaurants": 50}
    frozen["manifest_sha256"] = campaign.fingerprint(frozen)

    async def catalog(**kwargs):
        return [{"model": m, "json_mode": "object"} for m in ("a:free", "b:free")]

    monkeypatch.setattr(campaign, "discover_free_vision", catalog)
    seen = []

    def handler(request):
        if str(request.url).endswith("/key"):
            return httpx.Response(200, json={"data": {"free_model_daily_requests": {"remaining": 3}}})
        body = json.loads(request.content)
        seen.append(body)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert body["model"].endswith(":free") and "models" not in body
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": payload([r])}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": reported_cost},
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        campaign.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler))
    )
    result = asyncio.run(
        campaign.run_campaign(frozen, tmp_path, ["a:free", "b:free"], "test-key", max_calls=10)
    )
    assert result["calls"] == 1 and len(seen) == 1 and result["effective_max_calls"] == 1
    if reported_cost:
        assert result["status"] == "provider_or_billing_block"
        return
    assert result["status"] == "daily_or_invocation_budget_exhausted"
    # A modified frozen asset is refused before another inference.
    (tmp_path / asset).write_bytes(b"changed")
    with pytest.raises(ValueError, match="payload changed"):
        asyncio.run(campaign.run_campaign(frozen, tmp_path, ["a:free", "b:free"], "test-key", max_calls=10))
    assert len(seen) == 1


def test_numeric_id_equivalence_and_not_selected_are_not_bad_photos():
    r = restaurant(count=8)
    raw = json.loads(payload([r]))
    raw["restaurants"][0]["restaurant_id"] = 1
    parsed = campaign.parse_reply(json.dumps(raw), [r])["1"]
    result = campaign.restaurant_result(r, parsed)
    assert sum(p["decision"] == "not_selected" for p in result["results"]) == 2
    assert not any(p["decision"] == "reject" for p in result["results"])
