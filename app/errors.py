from uuid import UUID


class RecrUnionError(Exception):
    """Base class for expected application errors."""


class JobNotFoundError(RecrUnionError):
    """Raised when a requested job does not exist."""

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__("Job was not found.")
