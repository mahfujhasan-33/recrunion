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


class CompanyDocumentNotFoundError(RecrUnionError):
    code = "COMPANY_DOCUMENT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Company document was not found.")


class CompanyDocumentValidationError(RecrUnionError):
    code = "COMPANY_DOCUMENT_INVALID"
    status_code = 422


class CompanyDocumentTooLargeError(CompanyDocumentValidationError):
    code = "COMPANY_DOCUMENT_TOO_LARGE"
    status_code = 413


class CompanyDocumentDuplicateError(RecrUnionError):
    code = "COMPANY_DOCUMENT_DUPLICATE"
    status_code = 409


class CompanyDocumentBusyError(RecrUnionError):
    code = "COMPANY_DOCUMENT_BUSY"
    status_code = 409


class EmbeddingProviderError(RecrUnionError):
    code = "EMBEDDING_PROVIDER_UNAVAILABLE"
    status_code = 503
    retryable = True


class PolicyRetrievalError(RecrUnionError):
    code = "POLICY_RETRIEVAL_FAILED"
    status_code = 503


class PolicyReviewRequiredError(RecrUnionError):
    code = "POLICY_REVIEW_REQUIRED"
    status_code = 409


class PolicyEnhancementUnavailableError(RecrUnionError):
    code = "POLICY_ENHANCEMENT_UNAVAILABLE"
    status_code = 409


class ProcessingTaskNotFoundError(RecrUnionError):
    code = "PROCESSING_TASK_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Processing task was not found.")


class AssistantConversationNotFoundError(RecrUnionError):
    code = "ASSISTANT_CONVERSATION_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Assistant conversation was not found.")


class AssistantArtifactNotFoundError(RecrUnionError):
    code = "ASSISTANT_ARTIFACT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Assistant workspace item was not found.")


class AssistantActionError(RecrUnionError):
    code = "ASSISTANT_ACTION_INVALID"
    status_code = 409


class AssistantValidationError(RecrUnionError):
    code = "ASSISTANT_INPUT_INVALID"
    status_code = 422
