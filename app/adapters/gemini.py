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
from app.prompts.assistant import build_assistant_turn_prompt
from app.prompts.candidate_profile import build_candidate_profile_prompt
from app.prompts.candidate_screening import build_candidate_screening_prompt
from app.prompts.job_description import (
    build_job_description_enhancement_prompt,
    build_job_description_prompt,
    build_policy_alignment_prompt,
)
from app.schemas.assistant import AssistantTurnPlan, AssistantTurnRequest
from app.schemas.candidate_profiles import (
    CandidateProfileData,
    CandidateProfileExtractionRequest,
    CandidateProfileExtractionResult,
)
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionEnhancementRequest,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    LLMGenerationMetadata,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)
from app.schemas.policy_findings import PolicyAlignmentEvaluation
from app.schemas.screening import (
    CandidateScreeningEvaluation,
    CandidateScreeningEvaluationRequest,
    CandidateScreeningEvaluationResult,
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

    async def plan_assistant_turn(
        self,
        request: AssistantTurnRequest,
    ) -> AssistantTurnPlan:
        if not self._api_key:
            raise LLMConfigurationError("AI generation is not configured.")
        try:
            structured_model = self._build_model(temperature=0.3).with_structured_output(
                AssistantTurnPlan,
                method="json_schema",
                include_raw=True,
            )
            response = await structured_model.ainvoke(build_assistant_turn_prompt(request))
        except Exception as error:
            raise self._translate_provider_error(error) from None
        if not isinstance(response, Mapping):
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        parsed = response.get("parsed")
        if response.get("parsing_error") is not None or parsed is None:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        try:
            return (
                parsed
                if isinstance(parsed, AssistantTurnPlan)
                else AssistantTurnPlan.model_validate(parsed)
            )
        except ValidationError:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.") from None

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

    async def enhance_job_description(
        self,
        request: JobDescriptionEnhancementRequest,
    ) -> JobDescriptionGenerationResult:
        if not self._api_key:
            raise LLMConfigurationError("AI generation is not configured.")
        try:
            structured_model = self._build_model().with_structured_output(
                GeneratedJobDescription,
                method="json_schema",
                include_raw=True,
            )
            response = await structured_model.ainvoke(
                build_job_description_enhancement_prompt(request)
            )
        except Exception as error:
            translated_error = self._translate_provider_error(error)
            logger.warning(
                "Gemini job-description enhancement failed",
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

    async def evaluate_policy_alignment(
        self,
        request: PolicyAlignmentRequest,
    ) -> PolicyAlignmentResult:
        if not self._api_key:
            raise LLMConfigurationError("AI generation is not configured.")
        try:
            model = self._build_model()
            structured_model = model.with_structured_output(
                PolicyAlignmentEvaluation,
                method="json_schema",
                include_raw=True,
            )
            response = await structured_model.ainvoke(build_policy_alignment_prompt(request))
        except Exception as error:
            translated_error = self._translate_provider_error(error)
            logger.warning(
                "Gemini policy-alignment evaluation failed",
                extra={
                    "provider": "gemini",
                    "model": self._model_name,
                    "error_code": translated_error.code,
                },
            )
            raise translated_error from None
        return self._parse_policy_response(response)

    async def extract_candidate_profile(
        self,
        request: CandidateProfileExtractionRequest,
    ) -> CandidateProfileExtractionResult:
        if not self._api_key:
            raise LLMConfigurationError("AI generation is not configured.")
        try:
            structured_model = self._build_model(temperature=0.2).with_structured_output(
                CandidateProfileData,
                method="function_calling",
                include_raw=True,
            )
            response = await structured_model.ainvoke(build_candidate_profile_prompt(request))
        except Exception as error:
            translated_error = self._translate_provider_error(error)
            logger.warning(
                "Gemini candidate-profile extraction failed",
                extra={
                    "provider": "gemini",
                    "model": self._model_name,
                    "error_code": translated_error.code,
                },
            )
            raise translated_error from None
        if not isinstance(response, Mapping):
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        parsed = response.get("parsed")
        if response.get("parsing_error") is not None or parsed is None:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        try:
            profile = (
                parsed
                if isinstance(parsed, CandidateProfileData)
                else CandidateProfileData.model_validate(parsed)
            )
        except ValidationError:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.") from None
        raw = response.get("raw")
        response_metadata = getattr(raw, "response_metadata", {}) or {}
        return CandidateProfileExtractionResult(
            profile=profile,
            provider="gemini",
            model=str(response_metadata.get("model_name") or self._model_name),
        )

    async def evaluate_candidate_screening(
        self,
        request: CandidateScreeningEvaluationRequest,
    ) -> CandidateScreeningEvaluationResult:
        if not self._api_key:
            raise LLMConfigurationError("AI generation is not configured.")
        try:
            structured_model = self._build_model(temperature=0.2).with_structured_output(
                CandidateScreeningEvaluation,
                method="function_calling",
                include_raw=True,
            )
            response = await structured_model.ainvoke(build_candidate_screening_prompt(request))
        except Exception as error:
            translated_error = self._translate_provider_error(error)
            logger.warning(
                "Gemini candidate screening failed",
                extra={
                    "provider": "gemini",
                    "model": self._model_name,
                    "error_code": translated_error.code,
                },
            )
            raise translated_error from None
        if not isinstance(response, Mapping):
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        parsed = response.get("parsed")
        if response.get("parsing_error") is not None or parsed is None:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        try:
            evaluation = (
                parsed
                if isinstance(parsed, CandidateScreeningEvaluation)
                else CandidateScreeningEvaluation.model_validate(parsed)
            )
        except ValidationError:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.") from None
        raw = response.get("raw")
        response_metadata = getattr(raw, "response_metadata", {}) or {}
        return CandidateScreeningEvaluationResult(
            evaluation=evaluation,
            provider="gemini",
            model=str(response_metadata.get("model_name") or self._model_name),
        )

    def _build_model(self, *, temperature: float = 1.0) -> ChatGoogleGenerativeAI:
        return ChatGoogleGenerativeAI(
            model=self._model_name,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=0,
            temperature=temperature,
        )

    def _parse_policy_response(self, response: Any) -> PolicyAlignmentResult:
        if not isinstance(response, Mapping):
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        parsed = response.get("parsed")
        if response.get("parsing_error") is not None or parsed is None:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.")
        try:
            evaluation = (
                parsed
                if isinstance(parsed, PolicyAlignmentEvaluation)
                else PolicyAlignmentEvaluation.model_validate(parsed)
            )
        except ValidationError:
            raise LLMInvalidResponseError("The AI provider returned an invalid response.") from None
        raw = response.get("raw")
        response_metadata = getattr(raw, "response_metadata", {}) or {}
        usage_metadata = getattr(raw, "usage_metadata", {}) or {}
        return PolicyAlignmentResult(
            evaluation=evaluation,
            provider="gemini",
            model=str(response_metadata.get("model_name") or self._model_name),
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
