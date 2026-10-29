import asyncio
import json

import httpx
import pytest

from photo_separator.images import prepare
from photo_separator.providers import ProviderError, assess, cost_estimate, parse_assessment
from photo_separator.schemas import ProviderConfig
from tests.test_images import png
from tests.test_selection import assessment


def test_invalid_model_json_fails_closed():
    for value in [
        "{}",
        '{"confidence":99}',
        "not json",
        json.dumps(assessment().model_dump() | {"quality": 999}),
    ]:
        with pytest.raises(ProviderError):
            parse_assessment(value)


@pytest.mark.parametrize(
    "provider",
    ["openai", "deepseek", "gemini", "anthropic", "ollama", "compatible", "openrouter", "huggingface"],
)
def test_all_adapters_send_actual_image_and_extract_usage(provider):
    output = assessment().model_dump()
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        if provider == "openai":
            body = {
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(output)}]}],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            }
        elif provider == "anthropic":
            body = {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(output)}],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            }
        elif provider == "ollama":
            body = {
                "done": True,
                "done_reason": "stop",
                "message": {"content": json.dumps(output)},
                "prompt_eval_count": 100,
                "eval_count": 30,
            }
        else:
            body = {
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }
        return httpx.Response(200, json=body)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await assess(ProviderConfig(provider=provider, api_key="secret"), prepare(png()), client)

    result = asyncio.run(run())
    assert result.assessment.category == "interior" and result.usage["input_tokens"] == 100
    encoded = json.dumps(seen)
    assert "data:image/jpeg;base64," in encoded or '"images"' in encoded or '"base64"' in encoded


def test_provider_error_never_echoes_key_or_remote_response():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret-key"))
        ) as client:
            return await assess(
                ProviderConfig(provider="openai", api_key="secret-key"), prepare(png()), client
            )

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    assert "secret-key" not in str(error.value)


def test_incomplete_response_is_review_not_salvaged_json():
    body = {
        "choices": [
            {"finish_reason": "length", "message": {"content": json.dumps(assessment().model_dump())}}
        ]
    }

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ) as client:
            return await assess(ProviderConfig(provider="deepseek", api_key="secret"), prepare(png()), client)

    with pytest.raises(ProviderError):
        asyncio.run(run())


def test_unknown_cost_is_unknown_not_zero():
    assert cost_estimate(ProviderConfig(), {"input_tokens": 1000, "output_tokens": 100}) is None
    assert (
        cost_estimate(
            ProviderConfig(input_price=1, output_price=5), {"input_tokens": 1000, "output_tokens": 100}
        )
        == 0.0015
    )


@pytest.mark.parametrize(
    "provider,host,env_key",
    [
        ("openrouter", "openrouter.ai", "OPENROUTER_API_KEY"),
        ("huggingface", "router.huggingface.co", "HF_TOKEN"),
    ],
)
def test_remote_provider_uses_fixed_host_env_key_and_no_paid_fallback(provider, host, env_key, monkeypatch):
    monkeypatch.setenv(env_key, "test-only-secret")

    def handler(request):
        assert request.url.host == host
        assert request.headers["authorization"] == "Bearer test-only-secret"
        payload = json.loads(request.content)
        assert "models" not in payload
        assert "localhost" not in str(request.url)
        if provider == "openrouter":
            assert payload["provider"]["require_parameters"] is True
        return httpx.Response(
            200,
            json={
                "model": "resolved-model",
                "provider": "hosted-provider",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(assessment().model_dump())}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await assess(ProviderConfig(provider=provider), prepare(png()), client)

    result = asyncio.run(run())
    assert result.resolved_model == "resolved-model" and result.serving_provider == "hosted-provider"


def test_transport_diagnostic_keeps_exception_class_without_secret():
    def handler(request):
        raise httpx.ReadError("secret-from-remote-payload")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await assess(ProviderConfig(provider="openrouter", api_key="test-only"), prepare(png()), client)

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    assert "ReadError" in str(error.value) and "secret-from-remote" not in str(error.value)
