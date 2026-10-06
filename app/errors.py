from uuid import UUID


class RecrUnionError(Exception):
    """Base class for expected application errors."""

    code = "RECRUNION_ERROR"
    status_code = 400


class JobNotFoundError(RecrUnionError):
    """Raised when a requested job does not exist."""

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__("Job was not found.")

    code = "JOB_NOT_FOUND"
    status_code = 404


class InvalidJobStatusError(RecrUnionError):
    """Raised when a requested job action is invalid for its current status."""

    code = "INVALID_JOB_STATUS"
    status_code = 409


class JobDescriptionValidationError(RecrUnionError):
    """Raised when job-description input or generated output is invalid."""

    code = "JOB_DESCRIPTION_INVALID"
    status_code = 422


class LLMProviderError(RecrUnionError):
    """Base error for safe provider failure translation."""

    code = "LLM_PROVIDER_UNAVAILABLE"
    status_code = 502
    retryable = True


class LLMConfigurationError(LLMProviderError):
    """Raised when the configured LLM provider cannot be used."""

    code = "LLM_NOT_CONFIGURED"
    status_code = 503
    retryable = False


class LLMRateLimitError(LLMProviderError):
    """Raised when the provider rejects a request due to rate or quota limits."""

    code = "LLM_RATE_LIMITED"
    status_code = 503


class LLMTimeoutError(LLMProviderError):
    """Raised when the provider does not respond before the configured timeout."""

    code = "LLM_TIMEOUT"
    status_code = 504


class LLMInvalidResponseError(LLMProviderError):
    """Raised when the provider returns malformed structured output."""

    code = "LLM_INVALID_RESPONSE"
    status_code = 502
    retryable = False
