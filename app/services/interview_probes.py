import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.adapters.llm import LLMAdapter
from app.errors import InterviewAssistanceStateError, InterviewNotFoundError, RecrUnionError
from app.graphs.candidate_screening import contains_protected_screening_term
from app.models.interview_probes import AssistanceKind, InterviewAssistanceRun, ProbeStatus
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.models.screening import CandidateScreeningStatus, RequirementMatchStatus
from app.repositories.candidate_screening import CandidateScreeningRepository
from app.repositories.interview_probes import InterviewProbeRepository
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.repositories.transcripts import TranscriptRepository
from app.schemas.interview_probes import (
    AssistanceRunResponse,
    CopilotResponse,
    InterviewAssistanceResponse,
    ProbeGap,
    ProbeGenerationContext,
    ProbeResponse,
)
from app.schemas.processing_jobs import ProcessingJobResponse
from app.services.candidate_scoring import CandidateScoringService

ACTIVE = (ProcessingJobStatus.QUEUED, ProcessingJobStatus.RUNNING)


class InterviewProbeService:
    """Recruiter-only F11 use cases; no media, lifecycle, or scoring mutation."""

    def __init__(
        self,
        repository: InterviewProbeRepository,
        interviews: InterviewRepository,
        jobs: JobRepository,
        screenings: CandidateScreeningRepository,
        scoring: CandidateScoringService,
        transcripts: TranscriptRepository,
        llm: LLMAdapter,
    ) -> None:
        self.repository = repository
        self._interviews = interviews
        self._jobs = jobs
        self._screenings = screenings
        self._scoring = scoring
        self._transcripts = transcripts
        self._llm = llm

    def context(
        self, interview_id: UUID, *, transcript: bool = False
    ) -> tuple[ProbeGenerationContext, str]:
        self.repository.refresh_context()
        interview = self._interviews.get(interview_id)
        if interview is None:
            raise InterviewNotFoundError
        job = self._jobs.get(interview.job_id)
        screening = self._screenings.get_for_application(interview.application_id)
        if (
            job is None
            or job.status
            not in (
                JobStatus.APPROVED,
                JobStatus.PUBLISHING,
                JobStatus.PUBLISHED,
                JobStatus.PUBLISH_FAILED,
                JobStatus.CLOSED,
            )
            or not job.approved_at
            or not job.jd_content
            or screening is None
            or screening.status != CandidateScreeningStatus.COMPLETED
        ):
            raise InterviewAssistanceStateError(
                "Approved job requirements and completed F1/F2 results are required "
                "for interview assistance."
            )
        score = self._scoring.get_candidate_score(job.id, interview.application_id)
        if not score.is_current:
            raise InterviewAssistanceStateError(
                "Refresh the selected application's F1/F2 results before generating "
                "interview assistance."
            )
        gaps: list[ProbeGap] = []
        # Only objective F2-contributing requirements, never subjective excluded criteria.
        dimensions = {
            requirement.match_id: [dimension.dimension.value]
            for dimension in score.dimensions
            for requirement in dimension.requirements
        }
        f2_by_match = {
            requirement.match_id: dimension
            for dimension in score.dimensions
            for requirement in dimension.requirements
        }
        for match in screening.matches:
            if match.id not in dimensions or contains_protected_screening_term(
                match.requirement_text
            ):
                continue
            evidence = [
                item.candidate_cv_chunk_id
                for item in match.evidence
                if item.chunk.document_id == screening.candidate_document_id
            ]
            gap_reason = {
                RequirementMatchStatus.UNMET: (
                    "Submitted CV evidence does not yet demonstrate this requirement."
                ),
                RequirementMatchStatus.PARTIALLY_MET: (
                    "Submitted CV evidence only partially demonstrates this requirement."
                ),
                RequirementMatchStatus.MET: (
                    "Verify depth with a concrete example; F1 reports this requirement met."
                ),
            }[match.match_status]
            if not evidence:
                gap_reason += " No linked CV evidence is available; do not assume inability."
            gaps.append(
                ProbeGap(
                    requirement_id=match.job_requirement_id,
                    requirement_text=match.requirement_text,
                    match_id=match.id,
                    match_status=match.match_status.value,
                    required=match.requirement_type.value == "REQUIRED",
                    priority=match.priority,
                    reason=gap_reason,
                    score_id=score.score_id,
                    score_dimensions=dimensions[match.id],
                    f1_justification=minimize_excerpt(match.justification),
                    f2_evidence=[
                        {
                            "dimension": f2_by_match[match.id].dimension.value,
                            "raw_score": str(f2_by_match[match.id].raw_score),
                            "justification": minimize_excerpt(f2_by_match[match.id].justification),
                        }
                    ],
                    dimension_weight=float(f2_by_match[match.id].effective_weight or 0),
                    cv_evidence_ids=evidence,
                    cv_evidence=[
                        {
                            "chunk_id": str(item.candidate_cv_chunk_id),
                            "page": str(item.chunk.page_number),
                            "excerpt": minimize_excerpt(item.chunk.content),
                        }
                        for item in match.evidence
                        if item.candidate_cv_chunk_id in evidence
                    ][:3],
                )
            )
        if not gaps:
            raise InterviewAssistanceStateError(
                "No supported job-relevant F1/F2 requirement evidence is available."
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "job_version": job.jd_version,
                    "screened_at": timestamp_key(screening.screened_at),
                    "score_at": timestamp_key(score.calculated_at),
                    "config": score.scoring_config_version,
                    "gaps": [
                        gap.model_dump(mode="json")
                        for gap in sorted(gaps, key=lambda item: str(item.requirement_id))
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        segments = self._transcripts.list_for_interview(interview_id)[-30:] if transcript else []
        return ProbeGenerationContext(
            interview_id=interview_id,
            application_id=interview.application_id,
            job_title=job.title,
            jd_version=job.jd_version,
            gaps=gaps,
            current_probes=[probe.question for probe in self.repository.probes(interview_id)][:20],
            transcript=[
                {
                    "id": str(segment.id),
                    "role": segment.participant_role.value,
                    "text": minimize_excerpt(segment.text, limit=1500),
                }
                for segment in segments
                if minimize_excerpt(segment.text, limit=1500)
            ],
        ), fingerprint

    def queue(
        self,
        interview_id: UUID,
        request_id: UUID,
        *,
        regenerate: bool = False,
        message: str | None = None,
    ) -> AssistanceRunResponse:
        # Lock the owning interview so concurrent clicks cannot enqueue two runs.
        interview = self._interviews.get_for_update(interview_id)
        if interview is None:
            raise InterviewNotFoundError
        runs = self.repository.runs(interview_id)
        for run in runs:
            if run.request_id == request_id:
                return self._run_response(run)
        kind = AssistanceKind.FOLLOW_UP if message is not None else AssistanceKind.PROBES
        for run in runs:
            if run.kind == kind and self.repository.task(run).status in ACTIVE:
                return self._run_response(run)
        context, fingerprint = self.context(interview_id)
        if message:
            from app.graphs.interview_probes import ensure_safe_text

            ensure_safe_text(message)
        if kind == AssistanceKind.PROBES and not regenerate:
            existing = next(
                (
                    run
                    for run in runs
                    if run.kind == kind
                    and run.source_fingerprint == fingerprint
                    and self.repository.task(run).status == ProcessingJobStatus.COMPLETED
                ),
                None,
            )
            if existing:
                return self._run_response(existing)
        run_id, task_id = uuid4(), uuid4()
        task = ProcessingJob(
            id=task_id,
            job_type=ProcessingJobType.INTERVIEW_ASSISTANCE,
            entity_type="interview_assistance",
            entity_id=run_id,
            max_attempts=1,
            progress_message="Queued interview assistance",
        )
        run = InterviewAssistanceRun(
            id=run_id,
            interview_id=context.interview_id,
            request_id=request_id,
            task_id=task_id,
            kind=kind,
            source_fingerprint=fingerprint,
            request_text=message,
        )
        self.repository.queue(run, task)
        return self._run_response(run)

    async def process(self, run_id: UUID, report: Callable[[int, str], None]) -> None:
        from app.graphs.interview_probes import InterviewProbeGraph

        await InterviewProbeGraph(self, self._llm).run(run_id, report)

    def update_status(
        self, interview_id: UUID, probe_id: UUID, status: ProbeStatus
    ) -> ProbeResponse:
        if self._interviews.get_for_update(interview_id) is None:
            raise InterviewNotFoundError
        probe = next(
            (item for item in self.repository.probes(interview_id) if item.id == probe_id), None
        )
        if probe is None:
            raise InterviewNotFoundError
        if status == probe.status:
            return ProbeResponse.model_validate(probe)
        if probe.status != ProbeStatus.READY or status == ProbeStatus.READY:
            raise InterviewAssistanceStateError(
                "Only READY probes may be marked ASKED or DISMISSED. History cannot be reset."
            )
        self.repository.save_status(probe, status)
        return ProbeResponse.model_validate(probe)

    def get(self, interview_id: UUID) -> InterviewAssistanceResponse:
        interview = self._interviews.get(interview_id)
        if interview is None:
            raise InterviewNotFoundError
        application = self._interviews.get_application(interview.application_id)
        job = self._jobs.get(interview.job_id)
        profile = application.document.profile if application.document else None
        data = profile.structured_json if profile else {}
        try:
            context, fingerprint = self.context(interview_id)
            gaps = prioritize_gaps(context.gaps)
        except RecrUnionError:
            gaps, fingerprint = [], ""
        runs = self.repository.runs(interview_id)
        by_id = {run.id: run for run in runs}
        probes = [
            ProbeResponse.model_validate(item).model_copy(
                update={"is_current": by_id[item.run_id].source_fingerprint == fingerprint}
            )
            for item in self.repository.probes(interview_id)
        ]
        return InterviewAssistanceResponse(
            interview_id=interview_id,
            candidate_reference=application.candidate.display_reference,
            job_title=job.title,
            skills=[item["name"] for item in data.get("skills", [])][:12],
            experience=[item["role"] for item in data.get("work_history", [])][:4],
            qualifications=[item["qualification"] for item in data.get("education", [])][:4],
            gaps=gaps[:8],
            probes=probes,
            runs=[self._run_response(run) for run in runs[:10]],
        )

    def _run_response(self, run: InterviewAssistanceRun) -> AssistanceRunResponse:
        return AssistanceRunResponse(
            id=run.id,
            kind=run.kind,
            task=ProcessingJobResponse.model_validate(
                self.repository.task(run), from_attributes=True
            ),
            response=CopilotResponse.model_validate(run.response) if run.response else None,
        )


def prioritize_gaps(gaps: list[ProbeGap]) -> list[ProbeGap]:
    """Stable gaps-first ordering, then requiredness, F2 dimensions, and source priority."""
    severity = {"UNMET": 0, "PARTIALLY_MET": 1, "MET": 2}
    return sorted(
        gaps,
        key=lambda gap: (
            severity[gap.match_status],
            bool(gap.cv_evidence_ids),
            not gap.required,
            -gap.dimension_weight,
            gap.priority,
            str(gap.requirement_id),
        ),
    )


def minimize_excerpt(text: str, *, limit: int = 350) -> str:
    """Drop obvious contact lines; never send a complete CV or identity profile."""
    return " ".join(
        line
        for line in text.splitlines()
        if not contains_protected_screening_term(line)
        and not re.search(
            r"@|https?://|\+?\d[\d ()-]{7,}|\b(?:born|gender|religion|marital|nationality)\b",
            line,
            re.I,
        )
    )[:limit]


def timestamp_key(value: datetime) -> str:
    """Canonicalize SQLite naive UTC and PostgreSQL aware UTC equally across sessions."""
    return (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    ).isoformat()
