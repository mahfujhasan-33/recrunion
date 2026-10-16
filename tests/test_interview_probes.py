from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.adapters.gemini import GeminiLLMAdapter
from app.dependencies import build_interview_probe_service
from app.errors import (
    InterviewAssistanceStateError,
    InterviewAssistanceValidationError,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMProviderError,
)
from app.graphs.interview_probes import ensure_safe_text, validate_probes
from app.models.interview_probes import InterviewAssistanceRun, InterviewProbe, ProbeStatus
from app.models.interviews import (
    InterviewSession,
    InterviewStatus,
    InterviewTranscriptSegment,
    TranscriptParticipantRole,
)
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus
from app.models.scoring import CandidateScore
from app.models.screening import RequirementMatchStatus
from app.repositories.deletions import DeletionRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.services.interview_probes import prioritize_gaps
from app.services.interview_scheduling import hash_token
from tests.conftest import FakeLLMAdapter
from tests.test_candidate_processing_worker import worker_settings
from tests.test_candidate_scoring import build_service, create_application, create_job
from worker import main as worker_main


@pytest.fixture
def eligible(db_session, job_payload):
    job = create_job(db_session, job_payload)
    job.status = JobStatus.APPROVED
    job.approved_at = datetime.now(UTC)
    job.jd_content = "Approved synthetic backend role."
    job.jd_version = 1
    db_session.commit()
    statuses = {
        item.id: (
            RequirementMatchStatus.MET
            if index == 0
            else RequirementMatchStatus.PARTIALLY_MET
            if index == 1
            else RequirementMatchStatus.UNMET
        )
        for index, item in enumerate(job.requirements)
    }
    application, screening = create_application(
        db_session, job, statuses=statuses, evidence_requirement_ids={job.requirements[0].id}
    )
    build_service(db_session).score_candidate(job.id, application.id)
    interview = InterviewSession(
        id=uuid4(),
        job_id=job.id,
        application_id=application.id,
        candidate_id=application.candidate_id,
        status=InterviewStatus.SCHEDULED,
        scheduled_start_at=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=45,
        display_timezone="UTC",
        candidate_access_token_hash=hash_token("synthetic-f11-link"),
    )
    db_session.add(interview)
    db_session.commit()
    return job, application, screening, interview


async def finish(service, run, db_session):
    tasks = ProcessingJobRepository(db_session)
    task = tasks.get(run.task.id)
    task.status = ProcessingJobStatus.RUNNING
    db_session.commit()
    await service.process(run.id, lambda *_: None)
    tasks.complete(task)
    return service.get(db_session.get(InterviewAssistanceRun, run.id).interview_id)


async def test_generation_uses_persisted_f1_f2_prioritizes_and_persists(db_session, eligible):
    job, application, screening, interview = eligible
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    context, _ = service.context(interview.id)
    ordered = prioritize_gaps(context.gaps)
    assert ordered[0].match_status == "UNMET"
    assert ordered[-1].match_status == "MET"
    assert all(gap.match_id in {match.id for match in screening.matches} for gap in ordered)
    result = await finish(service, service.queue(interview.id, uuid4()), db_session)
    assert 3 <= len(result.probes) <= 5
    assert all(probe.status == ProbeStatus.READY for probe in result.probes)
    assert all(
        probe.question and probe.reason and probe.expected_evidence for probe in result.probes
    )
    assert all(
        probe.evidence_refs["score_id"] == str(context.gaps[0].score_id) for probe in result.probes
    )
    assert all(
        probe.requirement_id in {item.id for item in job.requirements} for probe in result.probes
    )
    assert db_session.scalar(select(func.count()).select_from(InterviewProbe)) == len(result.probes)


async def test_request_idempotency_regeneration_and_history(db_session, eligible):
    interview = eligible[-1]
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    request_id = uuid4()
    run = service.queue(interview.id, request_id)
    assert service.queue(interview.id, request_id).id == run.id
    assert service.queue(interview.id, uuid4()).id == run.id
    result = await finish(service, run, db_session)
    assert service.queue(interview.id, uuid4()).id == run.id
    asked, dismissed = result.probes[:2]
    assert (
        service.update_status(interview.id, asked.id, ProbeStatus.ASKED).status == ProbeStatus.ASKED
    )
    assert (
        service.update_status(interview.id, dismissed.id, ProbeStatus.DISMISSED).status
        == ProbeStatus.DISMISSED
    )
    assert (
        service.update_status(interview.id, asked.id, ProbeStatus.ASKED).status == ProbeStatus.ASKED
    )
    with pytest.raises(InterviewAssistanceStateError):
        service.update_status(interview.id, asked.id, ProbeStatus.READY)
    with pytest.raises(InterviewAssistanceStateError):
        service.update_status(interview.id, dismissed.id, ProbeStatus.ASKED)
    result = await finish(
        service, service.queue(interview.id, uuid4(), regenerate=True), db_session
    )
    assert {probe.id for probe in result.probes if probe.status != ProbeStatus.READY} == {
        asked.id,
        dismissed.id,
    }
    assert 3 <= len([probe for probe in result.probes if probe.status == ProbeStatus.READY]) <= 5


@pytest.mark.parametrize(
    "unsafe",
    [
        "What is your age?",
        "Do you have children?",
        "What religion do you practice?",
        "Rate the candidate score",
        "Recommend whether to hire",
        "Evaluate their accent",
    ],
)
def test_protected_and_assessment_requests_rejected(unsafe):
    with pytest.raises(InterviewAssistanceValidationError):
        ensure_safe_text(unsafe)


async def test_invalid_grounding_safety_and_incomplete_sets_are_not_persisted(db_session, eligible):
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    context, _ = service.context(eligible[-1].id)
    context.gaps = prioritize_gaps(context.gaps)[:5]
    result = await FakeLLMAdapter().generate_interview_probes(context)
    for variant in [
        result.model_copy(update={"probes": result.probes[:1]}),
        result.model_copy(
            update={
                "probes": [
                    result.probes[0].model_copy(
                        update={"question": "What religion do you practice?"}
                    ),
                    *result.probes[1:],
                ]
            }
        ),
        result.model_copy(
            update={
                "probes": [
                    result.probes[0].model_copy(update={"requirement_id": uuid4()}),
                    *result.probes[1:],
                ]
            }
        ),
        result.model_copy(
            update={
                "probes": [
                    result.probes[0].model_copy(update={"transcript_segment_ids": [uuid4()]}),
                    *result.probes[1:],
                ]
            }
        ),
        result.model_copy(
            update={
                "probes": [
                    result.probes[0].model_copy(
                        update={"question": "Describe your favourite lunch and hobby."}
                    ),
                    *result.probes[1:],
                ]
            }
        ),
    ]:
        with pytest.raises(InterviewAssistanceValidationError):
            validate_probes(variant, context, follow_up=False)
    assert db_session.scalar(select(func.count()).select_from(InterviewProbe)) == 0


async def test_provider_failure_leaves_video_transcript_scores_and_old_probes_intact(
    db_session, eligible
):
    class FailingLLM(FakeLLMAdapter):
        async def generate_interview_probes(self, request):
            raise LLMProviderError("Interview assistance is temporarily unavailable.")

    interview = eligible[-1]
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    old = await finish(service, service.queue(interview.id, uuid4()), db_session)
    score = db_session.scalar(select(CandidateScore))
    before = score.dimension_breakdown
    failing = build_interview_probe_service(db_session, FailingLLM())
    run = failing.queue(interview.id, uuid4(), regenerate=True)
    with pytest.raises(LLMProviderError):
        await failing.process(run.id, lambda *_: None)
    assert len(service.get(interview.id).probes) == len(old.probes)
    assert interview.status == InterviewStatus.SCHEDULED
    assert score.dimension_breakdown == before


async def test_copilot_context_uses_only_selected_application_and_transcript(db_session, eligible):
    job, application, _, interview = eligible
    other_application, _ = create_application(db_session, job)
    other = InterviewSession(
        id=uuid4(),
        job_id=job.id,
        application_id=other_application.id,
        candidate_id=other_application.candidate_id,
        status=InterviewStatus.SCHEDULED,
        scheduled_start_at=datetime.now(UTC),
        duration_minutes=45,
        display_timezone="UTC",
        candidate_access_token_hash=hash_token("other-synthetic-link"),
    )
    db_session.add(other)
    for owner, text in [
        (interview, "I debugged Python with structured logs."),
        (other, "OTHER-CANDIDATE-SECRET"),
    ]:
        db_session.add(
            InterviewTranscriptSegment(
                id=uuid4(),
                interview_id=owner.id,
                participant_role=TranscriptParticipantRole.CANDIDATE,
                participant_identity=f"candidate_{owner.id.hex}",
                segment_key="segment-1",
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                text=text,
                language="en",
                provider="fake",
            )
        )
    db_session.commit()
    captured = []

    class CapturingLLM(FakeLLMAdapter):
        async def generate_interview_probes(self, request):
            captured.append(request)
            return await super().generate_interview_probes(request)

    service = build_interview_probe_service(db_session, CapturingLLM())
    run = service.queue(
        interview.id, uuid4(), message="Suggest a deeper Python question about what was just said."
    )
    result = await finish(service, run, db_session)
    assert captured[0].application_id == application.id
    assert "OTHER-CANDIDATE-SECRET" not in captured[0].model_dump_json()
    assert "structured logs" in captured[0].model_dump_json()
    assert result.runs[0].response.transcript_segment_ids
    assert db_session.scalar(select(func.count()).select_from(InterviewProbe)) == 0
    assert db_session.scalar(select(func.count()).select_from(CandidateScore)) == 1
    with pytest.raises(Exception) as error:
        service.update_status(other.id, uuid4(), ProbeStatus.ASKED)
    assert "not found" in str(error.value).lower()


def test_api_auth_candidate_denial_csrf_and_recruiter_access(
    application, client, db_session, eligible
):
    interview = eligible[-1]
    base = f"/api/v1/interviews/{interview.id}"
    with TestClient(application) as anonymous:
        for path, method in [
            ("/probes", "GET"),
            ("/probes/generate", "POST"),
            ("/copilot", "POST"),
            (f"/probes/{uuid4()}", "PATCH"),
        ]:
            assert (
                anonymous.request(
                    method,
                    base + path,
                    headers={"Authorization": "Bearer synthetic-f11-link"},
                    json={},
                ).status_code
                == 401
            )
        assert anonymous.get("/interviews/join/synthetic-f11-link").status_code == 200
        assert anonymous.get(base + "/probes").status_code == 401
    assert client.get(base + "/probes").status_code == 200
    csrf = client.headers.pop("X-CSRF-Token")
    assert (
        client.post(base + "/probes/generate", json={"request_id": str(uuid4())}).status_code == 403
    )
    client.headers["X-CSRF-Token"] = csrf
    response = client.post(base + "/probes/generate", json={"request_id": str(uuid4())})
    assert response.status_code == 202
    assert response.json()["task"]["status"] == "QUEUED"
    assert client.get(f"/api/v1/interviews/{uuid4()}/probes").status_code == 404
    assert client.get(f"/interviews/{interview.id}/room").status_code == 200


def test_candidate_template_never_contains_copilot_or_candidate_context(
    application, client, eligible
):
    interview = eligible[-1]
    recruiter = client.get(f"/interviews/{interview.id}/room").text
    with TestClient(application) as anonymous:
        candidate = anonymous.get("/interviews/join/synthetic-f11-link").text
    for marker in [
        "AI Co-pilot",
        "interview-candidate-context",
        "interview_copilot.js",
        "Expected evidence",
        "copilot-tab-panel",
    ]:
        assert marker not in candidate
    for marker in [
        "AI Interview",
        "AI Co-pilot",
        "Live Transcript",
        "interview-candidate-context",
        "interview-elapsed",
        "remote-interview-video",
        "interview-transcript-segments",
    ]:
        assert marker in recruiter


def test_stale_or_unapproved_context_rejected(db_session, eligible):
    job, _, _, interview = eligible
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    job.status = JobStatus.DRAFT
    db_session.commit()
    with pytest.raises(InterviewAssistanceStateError):
        service.queue(interview.id, uuid4())
    job.status = JobStatus.APPROVED
    job.requirements[0].text = "Changed requirement"
    db_session.commit()
    with pytest.raises(InterviewAssistanceStateError):
        service.queue(interview.id, uuid4())


async def test_probe_status_api_and_mutation_csrf(application, client, db_session, eligible):
    interview = eligible[-1]
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    result = await finish(service, service.queue(interview.id, uuid4()), db_session)
    base = f"/api/v1/interviews/{interview.id}"
    csrf = client.headers.pop("X-CSRF-Token")
    assert (
        client.patch(f"{base}/probes/{result.probes[0].id}", json={"status": "ASKED"}).status_code
        == 403
    )
    assert (
        client.post(
            f"{base}/copilot", json={"request_id": str(uuid4()), "message": "Ask about Python"}
        ).status_code
        == 403
    )
    client.headers["X-CSRF-Token"] = csrf
    assert (
        client.patch(f"{base}/probes/{result.probes[0].id}", json={"status": "ASKED"}).status_code
        == 200
    )
    assert (
        client.patch(
            f"{base}/probes/{result.probes[0].id}", json={"status": "DISMISSED"}
        ).status_code
        == 409
    )
    assert client.patch(f"{base}/probes/{uuid4()}", json={"status": "ASKED"}).status_code == 404
    assert (
        client.post(
            f"{base}/copilot", json={"request_id": str(uuid4()), "message": "Ask about Python"}
        ).status_code
        == 202
    )


async def test_graph_rejects_unsafe_output_and_retries_transient_provider_failure(
    db_session, eligible
):
    class RetryingLLM(FakeLLMAdapter):
        calls = 0

        async def generate_interview_probes(self, request):
            self.calls += 1
            if self.calls == 1:
                raise LLMProviderError("Assistance temporarily unavailable.")
            return await super().generate_interview_probes(request)

    llm = RetryingLLM()
    service = build_interview_probe_service(db_session, llm)
    interview = eligible[-1]
    result = await finish(service, service.queue(interview.id, uuid4()), db_session)
    assert llm.calls == 2
    assert len(result.probes) >= 3

    class UnsafeLLM(FakeLLMAdapter):
        async def generate_interview_probes(self, request):
            generated = await super().generate_interview_probes(request)
            generated.probes[0].question = "What is your gender and family situation?"
            return generated

    service = build_interview_probe_service(db_session, UnsafeLLM())
    run = service.queue(interview.id, uuid4(), regenerate=True)
    with pytest.raises(InterviewAssistanceValidationError):
        await service.process(run.id, lambda *_: None)
    assert {item.id for item in service.get(interview.id).probes} == {
        item.id for item in result.probes
    }


async def test_transcript_dependent_request_requires_saved_evidence(db_session, eligible):
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    run = service.queue(
        eligible[-1].id, uuid4(), message="Ask a deeper question about the last answer."
    )
    with pytest.raises(InterviewAssistanceStateError, match="No saved transcript"):
        await service.process(run.id, lambda *_: None)


async def test_aggregate_deletion_removes_probes_runs_and_tasks(db_session, eligible):
    job, application, _, interview = eligible
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    run = service.queue(interview.id, uuid4())
    repository = DeletionRepository(db_session)
    assert repository.plan_application_deletion(job.id, application.id).is_busy
    await finish(service, run, db_session)
    plan = repository.plan_application_deletion(job.id, application.id)
    assert not plan.is_busy
    assert run.task.id in plan.task_ids
    repository.delete_application(plan)
    for model in (InterviewProbe, InterviewAssistanceRun, ProcessingJob):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("fail", [False, True])
def test_worker_assistance_dispatch_and_safe_failure(
    database_engine, db_session, eligible, tmp_path, monkeypatch, fail
):
    service = build_interview_probe_service(db_session, FakeLLMAdapter())
    run = service.queue(eligible[-1].id, uuid4())
    factory = sessionmaker(bind=database_engine, expire_on_commit=False)

    def build(session, _llm):
        class WorkerLLM(FakeLLMAdapter):
            async def generate_interview_probes(self, request):
                if fail:
                    raise LLMProviderError("Interview assistance is temporarily unavailable.")
                return await super().generate_interview_probes(request)

        return build_interview_probe_service(session, WorkerLLM())

    monkeypatch.setattr(worker_main, "SessionFactory", factory)
    monkeypatch.setattr(worker_main, "get_settings", lambda: worker_settings(tmp_path))
    monkeypatch.setattr(worker_main, "build_interview_probe_service", build)
    assert worker_main.process_next_task()
    db_session.expire_all()
    task = db_session.get(ProcessingJob, run.task.id)
    assert task.status == (ProcessingJobStatus.FAILED if fail else ProcessingJobStatus.COMPLETED)
    if fail:
        assert task.error_message_safe == "Interview assistance is temporarily unavailable."
        assert not service.get(eligible[-1].id).probes
    else:
        assert task.progress == 100
        assert len(service.get(eligible[-1].id).probes) >= 3


@pytest.mark.parametrize("case", ["valid", "invalid", "provider-error", "missing-key"])
async def test_gemini_f11_contract_is_structured_and_safe(db_session, eligible, monkeypatch, case):
    from app.schemas.interview_probes import GeneratedProbeSet

    context, _ = build_interview_probe_service(db_session, FakeLLMAdapter()).context(
        eligible[-1].id
    )
    context.gaps = context.gaps[:3]
    generated = await FakeLLMAdapter().generate_interview_probes(context)
    captured = []

    class StructuredModel:
        async def ainvoke(self, messages):
            captured.extend(messages)
            if case == "provider-error":
                raise RuntimeError("private-provider-details-not-for-client")
            return {
                "parsed": generated if case == "valid" else {"probes": []},
                "parsing_error": None,
            }

    def structured(schema, **options):
        assert schema == GeneratedProbeSet
        assert options["method"] == "json_schema"
        return StructuredModel()

    adapter = GeminiLLMAdapter(
        api_key="" if case == "missing-key" else "fake-key", model="fake-model", timeout_seconds=1
    )
    monkeypatch.setattr(
        adapter, "_build_model", lambda **_: SimpleNamespace(with_structured_output=structured)
    )
    if case == "valid":
        assert len((await adapter.generate_interview_probes(context)).probes) == 3
        assert str(context.application_id) in captured[-1].content
    else:
        expected = (
            LLMConfigurationError
            if case == "missing-key"
            else LLMInvalidResponseError
            if case == "invalid"
            else LLMProviderError
        )
        with pytest.raises(expected) as error:
            await adapter.generate_interview_probes(context)
        assert "private-provider-details" not in str(error.value)


@pytest.mark.parametrize("count", [1, 2, 3, 5])
async def test_probe_count_uses_available_supported_context_without_fabrication(
    db_session, eligible, count
):
    context, _ = build_interview_probe_service(db_session, FakeLLMAdapter()).context(
        eligible[-1].id
    )
    context.gaps = context.gaps[:count]
    result = await FakeLLMAdapter().generate_interview_probes(context)
    validate_probes(result, context, follow_up=False)
    assert len(result.probes) == min(count, len(context.gaps))


async def test_source_change_during_provider_wait_cannot_replace_probes(
    db_session, database_engine, eligible
):
    from sqlalchemy.orm import Session

    screening = eligible[2]

    class ChangingLLM(FakeLLMAdapter):
        async def generate_interview_probes(self, request):
            result = await super().generate_interview_probes(request)
            with Session(database_engine) as another_session:
                from app.models.screening import CandidateRequirementMatch

                match = another_session.get(CandidateRequirementMatch, screening.matches[0].id)
                match.justification = "Updated persisted evidence while provider was running."
                another_session.commit()
            return result

    service = build_interview_probe_service(db_session, ChangingLLM())
    run = service.queue(eligible[-1].id, uuid4())
    with pytest.raises(InterviewAssistanceStateError, match="Source evidence changed"):
        await service.process(run.id, lambda *_: None)
    assert db_session.scalar(select(func.count()).select_from(InterviewProbe)) == 0
