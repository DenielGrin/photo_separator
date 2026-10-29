import asyncio

from photo_separator import selection
from photo_separator.images import ImageError
from photo_separator.providers import ProviderError, ProviderResult
from photo_separator.schemas import ProviderConfig, SelectionRequest
from tests.test_images import png
from tests.test_selection import assessment


def test_duplicate_fetch_failure_and_provider_failure_never_forward(monkeypatch):
    calls = []

    def loader(url):
        if url.endswith("unreachable"):
            raise ImageError("blocked")
        return png(color="red" if url.endswith(("a", "duplicate")) else "blue")

    async def fake_assess(config, image, client):
        calls.append(image.digest)
        if len(calls) == 2:
            raise ProviderError("temporary failure")
        return ProviderResult(assessment(), {"input_tokens": 100, "output_tokens": 30}, "fake-model")

    monkeypatch.setattr(selection, "assess", fake_assess)
    request = SelectionRequest(
        restaurant_id="r",
        urls=[
            "https://example.com/a",
            "https://example.com/duplicate",
            "https://example.com/unreachable",
            "https://example.com/b",
        ],
        config=ProviderConfig(provider="technical", input_price=0, output_price=0),
    )
    result = asyncio.run(selection.select(request, loader=loader))
    assert len(calls) == 2
    assert result["cover"] == ["https://example.com/a"]
    assert [r["reason_code"] for r in result["results"]] == [
        "cover_candidate",
        "exact_duplicate",
        "fetch_failed",
        "provider_error",
    ]
    assert not result["cost_complete"]


def test_preview_uses_sanitized_local_image(monkeypatch, tmp_path):
    async def fake_assess(config, image, client):
        return ProviderResult(assessment(), {"input_tokens": 0, "output_tokens": 0}, "fake-model")

    monkeypatch.setattr(selection, "assess", fake_assess)
    request = SelectionRequest(
        restaurant_id="r", urls=["https://example.com/a"], config=ProviderConfig(provider="technical")
    )
    result = asyncio.run(selection.select(request, loader=lambda _: png(), preview_dir=tmp_path))
    preview = result["results"][0]["preview_url"]
    assert preview.startswith("/api/previews/")
    assert (tmp_path / (preview.split("/")[-1] + ".jpg")).read_bytes().startswith(b"\xff\xd8")
