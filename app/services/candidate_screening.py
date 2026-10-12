import logging
from collections.abc import Callable
from uuid import UUID, uuid4

from app.errors import (
    ApplicationNotFoundError,
    CandidateScreeningError,
    CandidateScreeningNotFoundError,
    CandidateScreeningStateError,
    JobNotFoundError,
    RecrUnionError,
)
from app.graphs.candidate_screening import (
    CandidateScreeningGraph,
    build_requirements_fingerprint,
)
from app.models.applications import CandidateDocumentProcessingStatus, JobApplication
from app.models.jobs import Job
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.models.screening import CandidateScreening, CandidateScreeningStatus
from app.repositories.candidate_processing import task_is_active
from app.repositories.candidate_screening import CandidateScreeningRepository
from app.repositories.jobs import JobRepository
from app.schemas.screening import (
    CandidateScreeningResponse,
    JobScreeningResponse,
    RequirementMatchResponse,
    ScreeningBatchResponse,
    ScreeningEvidenceResponse,
    ScreeningRankingEntry,
    ScreeningStartResponse,
    ScreeningSummary,
)

ProgressReporter = Callable[[int, str], None]
logger = logging.getLogger(__name__)


class CandidateScreeningService:
    """Queue, execute, retrieve, and deterministically rank F1 screenings."""

    def __init__(
        self,
        job_repository: JobRepository,
        repository: CandidateScreeningRepository,
        graph: CandidateScreeningGraph,
        *,
        worker_max_attempts: int,
    ) -> None:
        self._job_repository = job_repository
        self._repository = repository
        self._graph = graph
        self._worker_max_attempts = worker_max_attempts

    def queue(self, job_id: UUID, application_id: UUID) -> ScreeningStartResponse:
        job = self._get_job(job_id)
        application = self._repository.lock_application(job_id, application_id)
        if application is None:
            raise ApplicationNotFoundError
        return self._queue_application(job, application, reject_current=True)

    def queue_ready(self, job_id: UUID) -> ScreeningBatchResponse:
        job = self._get_job(job_id)
        applications = self._repository.list_applications(job_id)
        tasks: list[ScreeningStartResponse] = []
        eligible = 0
        current = 0
        active = 0
        ineligible = 0
        fingerprint = build_requirements_fingerprint(job.requirements)
        for application in applications:
            document = application.document
            if (
                document is None
                or document.processing_status != CandidateDocumentProcessingStatus.READY
            ):
                ineligible += 1
                continue
            eligible += 1
            screening = self._repository.get_for_application(application.id)
            if screening is not None and task_is_active(self._repository.current_task(screening)):
                active += 1
                continue
            if (
                screening is not None
                and screening.status == CandidateScreeningStatus.COMPLETED
                and screening.requirements_fingerprint == fingerprint
            ):
                current += 1
                continue
            locked = self._repository.lock_application(job_id, application.id)
            if locked is not None:
                tasks.append(self._queue_application(job, locked, reject_current=False))
        return ScreeningBatchResponse(
            eligible=eligible,
            queued=len(tasks),
            current=current,
            active=active,
            ineligible=ineligible,
            tasks=tasks,
        )

    async def process(self, screening_id: UUID, report: ProgressReporter) -> None:
        context = self._repository.get_context(screening_id)
        if context is None:
            raise CandidateScreeningNotFoundError
        self._repository.begin(context.screening)
        try:
            report(5, "Starting candidate screening")
            result = await self._graph.run(screening_id, report)
            if result.get("error") is not None:
                raise result["error"]
            report(96, "Candidate screening and ranking inputs are ready")
        except RecrUnionError as error:
            refreshed = self._repository.get_for_application(context.application.id)
            if refreshed is not None:
                self._repository.mark_failed(refreshed, code=error.code, message=str(error))
            raise
        except Exception as error:
            safe_error = CandidateScreeningError("Candidate screening could not be completed.")
            refreshed = self._repository.get_for_application(context.application.id)
            if refreshed is not None:
                self._repository.mark_failed(
                    refreshed,
                    code=safe_error.code,
                    message=str(safe_error),
                )
            logger.warning(
                "Candidate screening failed",
                extra={
                    "screening_id": str(screening_id),
                    "operation": "candidate_screening",
                    "error_code": safe_error.code,
                },
            )
            raise safe_error from error

    def get_screening(
        self,
        job_id: UUID,
        application_id: UUID,
    ) -> CandidateScreeningResponse:
        self._get_job(job_id)
        applications = self._repository.list_applications(job_id)
        application_by_id = {item.id: item for item in applications}
        application = application_by_id.get(application_id)
        if application is None:
            raise ApplicationNotFoundError
        screening = self._repository.get_for_application(application_id)
        if screening is None:
            raise CandidateScreeningNotFoundError
        ranking = self._build_ranking(job_id, applications)
        rank_entry = next(
            (
                item
                for item in [*ranking.ranked, *ranking.unranked]
                if item.application_id == application_id
            ),
            None,
        )
        return self._to_detail(screening, application, rank_entry)

    def get_job_screening(self, job_id: UUID) -> JobScreeningResponse:
        self._get_job(job_id)
        return self._build_ranking(job_id, self._repository.list_applications(job_id))

    def _queue_application(
        self,
        job: Job,
        application: JobApplication,
        *,
        reject_current: bool,
    ) -> ScreeningStartResponse:
        document = application.document
        if (
            document is None
            or document.processing_status != CandidateDocumentProcessingStatus.READY
        ):
            raise CandidateScreeningStateError("Candidate screening requires a READY processed CV.")
        fingerprint = build_requirements_fingerprint(job.requirements)
        screening = self._repository.lock_for_application(application.id)
        if screening is not None and task_is_active(self._repository.current_task(screening)):
            raise CandidateScreeningStateError("Candidate screening is already queued or running.")
        if (
            reject_current
            and screening is not None
            and screening.status == CandidateScreeningStatus.COMPLETED
            and screening.requirements_fingerprint == fingerprint
        ):
            raise CandidateScreeningStateError(
                "Candidate screening is already current for these job requirements."
            )
        if screening is None:
            screening = CandidateScreening(
                id=uuid4(),
                job_id=job.id,
                application_id=application.id,
                candidate_document_id=document.id,
                requirements_fingerprint=fingerprint,
            )
        else:
            screening.candidate_document_id = document.id
            screening.requirements_fingerprint = fingerprint
        task = ProcessingJob(
            id=uuid4(),
            job_type=ProcessingJobType.SCREEN_CANDIDATE_APPLICATION,
            entity_type="CANDIDATE_SCREENING",
            entity_id=screening.id,
            max_attempts=self._worker_max_attempts,
            progress_message="Waiting to screen candidate evidence",
        )
        screening = self._repository.queue(screening, task)
        return ScreeningStartResponse(
            application_id=application.id,
            screening_id=screening.id,
            task_id=task.id,
            status=screening.status,
        )

    def _build_ranking(
        self,
        job_id: UUID,
        applications: list[JobApplication],
    ) -> JobScreeningResponse:
        job = self._get_job(job_id)
        fingerprint = build_requirements_fingerprint(job.requirements)
        screenings = {item.application_id: item for item in self._repository.list_for_job(job_id)}
        current = [
            screening
            for screening in screenings.values()
            if screening.status == CandidateScreeningStatus.COMPLETED
            and screening.requirements_fingerprint == fingerprint
        ]
        current.sort(key=ranking_key)
        rank_by_application = {
            screening.application_id: index for index, screening in enumerate(current, start=1)
        }
        ranked: list[ScreeningRankingEntry] = []
        previous: CandidateScreening | None = None
        application_by_id = {item.id: item for item in applications}
        for screening in current:
            application = application_by_id[screening.application_id]
            ranked.append(
                self._ranking_entry(
                    application,
                    screening,
                    rank=rank_by_application[screening.application_id],
                    is_current=True,
                    explanation=ranking_explanation(screening, previous),
                )
            )
            previous = screening
        unranked: list[ScreeningRankingEntry] = []
        for application in applications:
            if application.id in rank_by_application:
                continue
            screening = screenings.get(application.id)
            is_current = bool(
                screening is not None and screening.requirements_fingerprint == fingerprint
            )
            unranked.append(
                self._ranking_entry(
                    application,
                    screening,
                    rank=None,
                    is_current=is_current,
                    explanation=unranked_explanation(screening, is_current),
                )
            )
        return JobScreeningResponse(job_id=job_id, ranked=ranked, unranked=unranked)

    @staticmethod
    def _ranking_entry(
        application: JobApplication,
        screening: CandidateScreening | None,
        *,
        rank: int | None,
        is_current: bool,
        explanation: str,
    ) -> ScreeningRankingEntry:
        has_results = bool(
            screening is not None and screening.status == CandidateScreeningStatus.COMPLETED
        )
        return ScreeningRankingEntry(
            application_id=application.id,
            candidate_reference=application.candidate.display_reference,
            screening_id=screening.id if screening else None,
            screening_status=screening.status if screening else None,
            is_current=is_current,
            rank=rank,
            ranking_explanation=explanation,
            required=summary_for(screening, required=True) if has_results else None,
            preferred=summary_for(screening, required=False) if has_results else None,
            safe_error_message=screening.safe_error_message if screening else None,
        )

    @staticmethod
    def _to_detail(
        screening: CandidateScreening,
        application: JobApplication,
        ranking: ScreeningRankingEntry | None,
    ) -> CandidateScreeningResponse:
        has_results = screening.status == CandidateScreeningStatus.COMPLETED
        matches = (
            [
                RequirementMatchResponse(
                    requirement_id=match.job_requirement_id,
                    category=match.category,
                    requirement_type=match.requirement_type,
                    requirement_text=match.requirement_text,
                    minimum_value=match.minimum_value,
                    status=match.match_status,
                    justification=match.justification,
                    evidence=[
                        ScreeningEvidenceResponse(
                            chunk_id=item.candidate_cv_chunk_id,
                            page_number=item.chunk.page_number,
                            excerpt=item.chunk.content[:700],
                            similarity=item.retrieval_similarity,
                        )
                        for item in match.evidence
                    ],
                )
                for match in screening.matches
            ]
            if has_results
            else []
        )
        return CandidateScreeningResponse(
            screening_id=screening.id,
            job_id=screening.job_id,
            application_id=screening.application_id,
            candidate_reference=application.candidate.display_reference,
            status=screening.status,
            is_current=ranking.is_current if ranking else False,
            rank=ranking.rank if ranking else None,
            ranking_explanation=ranking.ranking_explanation if ranking else None,
            required=summary_for(screening, required=True) if has_results else None,
            preferred=summary_for(screening, required=False) if has_results else None,
            matches=matches,
            safe_error_code=screening.safe_error_code,
            safe_error_message=screening.safe_error_message,
            screened_at=screening.screened_at,
        )

    def _get_job(self, job_id: UUID) -> Job:
        job = self._job_repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job


def ranking_key(screening: CandidateScreening) -> tuple[int, int, int, int, int, str]:
    return (
        screening.required_unmet,
        screening.required_partially_met,
        -screening.required_met,
        -screening.preferred_met,
        -screening.preferred_partially_met,
        screening.application_id.hex,
    )


def ranking_explanation(
    screening: CandidateScreening,
    previous: CandidateScreening | None,
) -> str:
    if previous is None:
        return (
            "Ranks first under the deterministic F1 ordering: required outcomes are considered "
            "before preferred outcomes."
        )
    comparisons = (
        (screening.required_unmet, previous.required_unmet, "more unmet required requirements"),
        (
            screening.required_partially_met,
            previous.required_partially_met,
            "more partially met required requirements",
        ),
        (-screening.required_met, -previous.required_met, "fewer met required requirements"),
        (-screening.preferred_met, -previous.preferred_met, "fewer met preferred requirements"),
        (
            -screening.preferred_partially_met,
            -previous.preferred_partially_met,
            "fewer partially met preferred requirements",
        ),
    )
    for current_value, previous_value, reason in comparisons:
        if current_value != previous_value:
            return f"Ranks below the preceding candidate because this screening has {reason}."
    return "The F1 outcomes are tied; the stable application identifier breaks the tie."


def unranked_explanation(
    screening: CandidateScreening | None,
    is_current: bool,
) -> str:
    if screening is None:
        return "Not ranked — screening has not been completed."
    if not is_current:
        return "Not ranked — job requirements changed and this screening is stale."
    if screening.status == CandidateScreeningStatus.FAILED:
        return "Not ranked — screening failed and requires an explicit retry."
    if screening.status in {
        CandidateScreeningStatus.QUEUED,
        CandidateScreeningStatus.PROCESSING,
    }:
        return "Not ranked — screening is incomplete."
    return "Not ranked — no current completed screening is available."


def summary_for(
    screening: CandidateScreening,
    *,
    required: bool,
) -> ScreeningSummary:
    if required:
        return ScreeningSummary(
            met=screening.required_met,
            partially_met=screening.required_partially_met,
            unmet=screening.required_unmet,
        )
    return ScreeningSummary(
        met=screening.preferred_met,
        partially_met=screening.preferred_partially_met,
        unmet=screening.preferred_unmet,
    )
