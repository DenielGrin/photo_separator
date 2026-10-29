from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Assessment(StrictModel):
    category: Literal["interior", "exterior", "food", "drink", "people", "menu", "receipt", "other"]
    quality: float = Field(ge=0, le=100)
    appeal: float = Field(ge=0, le=100)
    relevance: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    people: Literal["none", "background", "prominent"]
    text_dominant: bool
    severe_defect: bool
    unsafe: bool
    reason: str = Field(min_length=1, max_length=800)


class ProviderConfig(StrictModel):
    provider: Literal[
        "ollama",
        "openai",
        "deepseek",
        "gemini",
        "anthropic",
        "openrouter",
        "huggingface",
        "compatible",
        "technical",
    ] = "openrouter"
    model: str = Field(
        default="google/gemma-4-31b-it:free", min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9_./:\-]+$"
    )
    api_key: SecretStr | None = None
    json_mode: Literal["object", "prompt"] = "object"
    # Endpoint is operator controlled through server environment, never through an untrusted request.
    input_price: float | None = Field(default=None, ge=0)
    output_price: float | None = Field(default=None, ge=0)


class Policy(StrictModel):
    min_score: float = Field(default=75, ge=0, le=100)
    min_confidence: float = Field(default=0.85, ge=0, le=1)
    max_covers: int = Field(default=6, ge=3, le=6)
    people_policy: Literal["strict", "review_background", "allow_background"] = "review_background"
    min_short_side: int = Field(default=480, ge=64, le=2000)
    diversity: bool = True


class SelectionRequest(StrictModel):
    restaurant_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    urls: list[str] = Field(min_length=1, max_length=30)
    config: ProviderConfig = Field(default_factory=ProviderConfig)
    policy: Policy = Field(default_factory=Policy)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, urls):
        if any(len(u) > 8192 or not u.startswith(("https://", "http://")) for u in urls):
            raise ValueError("Expected HTTP(S) image URLs, at most 8192 characters each")
        if len(set(urls)) != len(urls):
            raise ValueError("Duplicate URLs; supply each URL once")
        return urls


class SaveRequest(StrictModel):
    run_id: str
    expected_revision: int = Field(ge=0)


class ManualRequest(StrictModel):
    url: str = Field(max_length=8192)
    action: Literal["pin", "exclude", "reset"]
    expected_revision: int = Field(ge=0)
