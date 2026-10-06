import logging
from collections.abc import Mapping
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError

from app.errors import (
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.prompts.job_description import build_job_description_prompt
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    LLMGenerationMetadata,
)

logger = logging.getLogger(__name__)


class GeminiLLMAdapter:
    """Generate structured job descriptions through LangChain Gemini."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self._api_key = api_key.strip()
        self._model_name = model
        self._timeout_seconds = timeout_seconds

    async def generate_job_description(
        self,
        request: JobDescriptionGenerationRequest,
    ) -> JobDescriptionGenerationResult:
        if not self._api_key:
            raise LLMConfigurationError("AI generation is not configured.")

        try:
            model = ChatGoogleGenerativeAI(
                model=self._model_name,
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                max_retries=0,
                temperature=1.0,
            )
            structured_model = model.with_structured_output(
                GeneratedJobDescription,
                method="json_schema",
                include_raw=True,
            )
            response = await structured_model.ainvoke(build_job_description_prompt(request))
        except Exception as error:
            translated_error = self._translate_provider_error(error)
            logger.warning(
                "Gemini job-description generation failed",
                extra={
                    "provider": "gemini",
                    "model": self._model_name,
                    "error_code": translated_error.code,
                },
            )
            raise translated_error from None

        return self._parse_response(response)

    def _parse_response(self, response: Any) -> JobDescriptionGenerationResult:
        if not isinstance(response, Mapping):
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")

        parsing_error = response.get("parsing_error")
        parsed = response.get("parsed")
        if parsing_error is not None or parsed is None:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")

        try:
            description = (
                parsed
                if isinstance(parsed, GeneratedJobDescription)
                else GeneratedJobDescription.model_validate(parsed)
            )
        except ValidationError:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.") from None

        raw = response.get("raw")
        response_metadata = getattr(raw, "response_metadata", {}) or {}
        usage_metadata = getattr(raw, "usage_metadata", {}) or {}
        provider_model = response_metadata.get("model_name") or self._model_name

        return JobDescriptionGenerationResult(
            description=description,
            provider="gemini",
            model=str(provider_model),
            metadata=LLMGenerationMetadata(
                finish_reason=self._safe_text(response_metadata.get("finish_reason")),
                input_tokens=self._safe_int(usage_metadata.get("input_tokens")),
                output_tokens=self._safe_int(usage_metadata.get("output_tokens")),
                total_tokens=self._safe_int(usage_metadata.get("total_tokens")),
            ),
        )

    @staticmethod
    def _translate_provider_error(error: Exception) -> LLMProviderError:
        if isinstance(error, LLMProviderError):
            return error
        if isinstance(error, TimeoutError):
            return LLMTimeoutError("The AI provider timed out. Please try again.")

        status_code = GeminiLLMAdapter._status_code(error)
        if status_code == 429:
            return LLMRateLimitError(
                "The AI provider is temporarily rate limited. Please try again later."
            )
        if status_code in {401, 403}:
            return LLMConfigurationError("AI generation is not configured correctly.")
        return LLMProviderError("The AI provider is temporarily unavailable.")

    @staticmethod
    def _status_code(error: Exception) -> int | None:
        for attribute in ("status_code", "code"):
            value = getattr(error, attribute, None)
            if isinstance(value, int):
                return value
            enum_value = getattr(value, "value", None)
            if isinstance(enum_value, int):
                return enum_value
        return None

    @staticmethod
    def _safe_int(value: object) -> int | None:
        return value if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _safe_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text[:100]
