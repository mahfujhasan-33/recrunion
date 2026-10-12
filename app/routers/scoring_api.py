from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.dependencies import get_candidate_scoring_service
from app.schemas.scoring import (
    CandidateScoreResponse,
    JobScoresResponse,
    ScoreBatchResponse,
    ScoringConfigResponse,
    ScoringConfigUpdate,
)
from app.services.candidate_scoring import CandidateScoringService

router = APIRouter(prefix="/api/v1/jobs/{job_id}", tags=["candidate scoring"])
CandidateScoringServiceDependency = Annotated[
    CandidateScoringService,
    Depends(get_candidate_scoring_service),
]


@router.get("/scoring-config", response_model=ScoringConfigResponse)
def get_scoring_config(
    job_id: UUID,
    service: CandidateScoringServiceDependency,
) -> ScoringConfigResponse:
    return service.get_config(job_id)


@router.put("/scoring-config", response_model=ScoringConfigResponse)
def update_scoring_config(
    job_id: UUID,
    payload: ScoringConfigUpdate,
    service: CandidateScoringServiceDependency,
) -> ScoringConfigResponse:
    return service.update_config(job_id, payload)


@router.post("/applications/score-screened", response_model=ScoreBatchResponse)
def score_screened_applications(
    job_id: UUID,
    service: CandidateScoringServiceDependency,
) -> ScoreBatchResponse:
    return service.score_screened(job_id)


@router.post(
    "/applications/{application_id}/score",
    response_model=CandidateScoreResponse,
)
def score_application(
    job_id: UUID,
    application_id: UUID,
    service: CandidateScoringServiceDependency,
) -> CandidateScoreResponse:
    return service.score_candidate(job_id, application_id)


@router.get("/scores", response_model=JobScoresResponse)
def get_job_scores(
    job_id: UUID,
    service: CandidateScoringServiceDependency,
) -> JobScoresResponse:
    return service.get_job_scores(job_id)


@router.get(
    "/applications/{application_id}/score",
    response_model=CandidateScoreResponse,
)
def get_application_score(
    job_id: UUID,
    application_id: UUID,
    service: CandidateScoringServiceDependency,
) -> CandidateScoreResponse:
    return service.get_candidate_score(job_id, application_id)
