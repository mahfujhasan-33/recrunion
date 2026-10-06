from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.adapters import gemini
from app.adapters.gemini import GeminiLLMAdapter
from app.adapters.llm import LLMAdapter
from app.errors import LLMConfigurationError, LLMInvalidResponseError, LLMRateLimitError
from app.schemas.job_descriptions import JobDescriptionGenerationRequest


def generation_request() -> JobDescriptionGenerationRequest:
    return JobDescriptionGenerationRequest(
        job_id=uuid4(),
        title="Backend Engineer",
        location="Dhaka",
        employment_type="FULL_TIME",
        application_email="jobs@example.com",
        required_skills=["Python"],
        preferred_skills=["FastAPI"],
        minimum_experience=3,
        qualifications=["Bachelor's degree or equivalent experience"],
        selection_criteria=["Production API ownership"],
    )


class FakeStructuredGemini:
    async def ainvoke(self, messages: object) -> dict[str, object]:
        assert messages
        return {
            "parsed": {
                "title": "Backend Engineer",
                "summary": "Build reliable recruitment services.",
                "responsibilities": ["Own backend API delivery."],
                "required_skills": ["Python"],
                "preferred_skills": ["FastAPI"],
                "qualifications": ["Bachelor's degree or equivalent experience"],
                "minimum_experience": 3,
                "application_information": "Apply at jobs@example.com.",
            },
            "raw": SimpleNamespace(
                response_metadata={"finish_reason": "STOP", "model_name": "test-model"},
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                },
            ),
            "parsing_error": None,
        }


class FakeGeminiClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def with_structured_output(self, *args: object, **kwargs: object) -> FakeStructuredGemini:
        assert args
        assert kwargs["method"] == "json_schema"
        return FakeStructuredGemini()


@pytest.mark.asyncio
async def test_gemini_adapter_satisfies_contract_and_parses_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini, "ChatGoogleGenerativeAI", FakeGeminiClient)
    adapter = GeminiLLMAdapter(
        api_key="test-key",
        model="test-model",
        timeout_seconds=10,
    )

    result = await adapter.generate_job_description(generation_request())

    assert isinstance(adapter, LLMAdapter)
    assert result.description.required_skills == ["Python"]
    assert result.model == "test-model"
    assert result.metadata.total_tokens == 30


@pytest.mark.asyncio
async def test_gemini_adapter_translates_missing_key_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GeminiLLMAdapter(
        api_key="",
        model="test-model",
        timeout_seconds=10,
    )
    with pytest.raises(LLMConfigurationError):
        await adapter.generate_job_description(generation_request())

    class RateLimitError(Exception):
        status_code = 429

    class FailingStructuredGemini:
        async def ainvoke(self, messages: object) -> None:
            raise RateLimitError

    class FailingGeminiClient(FakeGeminiClient):
        def with_structured_output(
            self,
            *args: object,
            **kwargs: object,
        ) -> FailingStructuredGemini:
            return FailingStructuredGemini()

    monkeypatch.setattr(gemini, "ChatGoogleGenerativeAI", FailingGeminiClient)
    configured_adapter = GeminiLLMAdapter(
        api_key="test-key",
        model="test-model",
        timeout_seconds=10,
    )
    with pytest.raises(LLMRateLimitError):
        await configured_adapter.generate_job_description(generation_request())


@pytest.mark.asyncio
async def test_gemini_adapter_rejects_malformed_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedStructuredGemini:
        async def ainvoke(self, messages: object) -> dict[str, object]:
            return {
                "parsed": None,
                "raw": SimpleNamespace(response_metadata={}, usage_metadata={}),
                "parsing_error": ValueError("raw provider parsing detail"),
            }

    class MalformedGeminiClient(FakeGeminiClient):
        def with_structured_output(
            self,
            *args: object,
            **kwargs: object,
        ) -> MalformedStructuredGemini:
            return MalformedStructuredGemini()

    monkeypatch.setattr(gemini, "ChatGoogleGenerativeAI", MalformedGeminiClient)
    adapter = GeminiLLMAdapter(
        api_key="test-key",
        model="test-model",
        timeout_seconds=10,
    )

    with pytest.raises(LLMInvalidResponseError) as error:
        await adapter.generate_job_description(generation_request())

    assert "raw provider parsing detail" not in str(error.value)
