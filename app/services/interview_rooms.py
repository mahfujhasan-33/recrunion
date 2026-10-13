import logging
from datetime import UTC, datetime
from uuid import UUID

from app.adapters.video import (
    VideoAdapter,
    VideoParticipantRole,
    video_participant_identity,
)
from app.errors import (
    InterviewLifecycleError,
    InterviewNotFoundError,
    InterviewProviderCleanupError,
    InterviewSchedulingError,
    JobNotFoundError,
    VideoProviderError,
)
from app.models.applications import JobApplication
from app.models.interviews import InterviewSession, InterviewStatus
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.schemas.interviews import InterviewRoomView
from app.services.interview_scheduling import ensure_utc, hash_token

logger = logging.getLogger(__name__)


class InterviewRoomService:
    """Authorize LiveKit room access and own persisted interview lifecycle."""

    def __init__(
        self,
        interview_repository: InterviewRepository,
        job_repository: JobRepository,
        video_adapter: VideoAdapter,
    ) -> None:
        self._interviews = interview_repository
        self._jobs = job_repository
        self._video = video_adapter

    def get_candidate_room(self, invitation_token: str) -> InterviewRoomView:
        interview = self._get_candidate_interview(invitation_token)
        return self._room_view(
            interview,
            VideoParticipantRole.CANDIDATE,
            lifecycle_sync_url=f"/interviews/join/{invitation_token}/lifecycle-sync",
        )

    def get_interviewer_room(self, interview_id: UUID) -> InterviewRoomView:
        interview = self._get_interview(interview_id)
        return self._room_view(
            interview,
            VideoParticipantRole.INTERVIEWER,
            lifecycle_sync_url=f"/api/v1/interviews/{interview.id}/lifecycle-sync",
            end_url=f"/api/v1/interviews/{interview.id}/end",
        )

    async def sync_candidate_lifecycle(self, invitation_token: str) -> InterviewSession:
        interview = self._get_candidate_interview(invitation_token)
        return await self._sync_lifecycle(interview.id)

    async def sync_interviewer_lifecycle(self, interview_id: UUID) -> InterviewSession:
        self._get_interview(interview_id)
        return await self._sync_lifecycle(interview_id)

    async def end_interview(
        self,
        interview_id: UUID,
        role: VideoParticipantRole,
        *,
        now: datetime | None = None,
    ) -> InterviewSession:
        """Persist formal completion before terminating the provider room."""

        if role != VideoParticipantRole.INTERVIEWER:
            raise InterviewLifecycleError("Only the interviewer can end the interview.")
        interview = self._get_interview_for_update(interview_id)
        if interview.status == InterviewStatus.ENDED:
            return interview
        if interview.status == InterviewStatus.SCHEDULED:
            raise InterviewLifecycleError("The interview has not been joined yet.")
        interview.status = InterviewStatus.ENDED
        if interview.ended_at is None:
            interview.ended_at = now or datetime.now(UTC)
        persisted = self._interviews.save(interview)
        try:
            await self._video.terminate_room(interview_id)
        except VideoProviderError as error:
            logger.warning(
                "LiveKit room cleanup failed after interview end interview_id=%s",
                interview_id,
            )
            raise InterviewProviderCleanupError(
                "The interview ended, but the live room could not be closed cleanly."
            ) from error
        return persisted

    async def _sync_lifecycle(self, interview_id: UUID) -> InterviewSession:
        interview = self._get_interview(interview_id)
        if interview.status == InterviewStatus.ENDED:
            return interview

        participants = await self._video.list_participants(interview_id)
        identities = {participant.identity for participant in participants}
        expected_humans = {
            video_participant_identity(interview_id, VideoParticipantRole.CANDIDATE),
            video_participant_identity(interview_id, VideoParticipantRole.INTERVIEWER),
        }
        human_count = len(identities & expected_humans)

        interview = self._get_interview_for_update(interview_id)
        if interview.status == InterviewStatus.ENDED:
            return interview
        changed = False
        if human_count >= 2 and interview.status != InterviewStatus.ACTIVE:
            interview.status = InterviewStatus.ACTIVE
            if interview.started_at is None:
                interview.started_at = datetime.now(UTC)
            changed = True
        elif human_count == 1 and interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.WAITING
            changed = True
        return self._interviews.save(interview) if changed else interview

    def _room_view(
        self,
        interview: InterviewSession,
        role: VideoParticipantRole,
        *,
        lifecycle_sync_url: str,
        end_url: str | None = None,
    ) -> InterviewRoomView:
        job = self._jobs.get(interview.job_id)
        if job is None:
            raise JobNotFoundError(interview.job_id)
        application = self._get_application(interview)
        provider_ready = self._video.configured
        join_allowed = interview.status != InterviewStatus.ENDED and provider_ready
        credentials = (
            self._video.create_participant_credentials(interview.id, role) if join_allowed else None
        )
        return InterviewRoomView(
            interview_id=interview.id,
            job_title=job.title,
            candidate_reference=(
                application.candidate.display_reference
                if role == VideoParticipantRole.INTERVIEWER
                else None
            ),
            role=role,
            status=interview.status,
            scheduled_start_at=ensure_utc(interview.scheduled_start_at),
            duration_minutes=interview.duration_minutes,
            timezone=interview.display_timezone,
            livekit_url=credentials.provider_url if credentials else None,
            participant_token=credentials.participant_token if credentials else None,
            participant_identity=credentials.participant_identity if credentials else None,
            provider_configured=provider_ready,
            provider_error_message=(
                None
                if provider_ready
                else (
                    "Live interviews are not configured. "
                    "Ask the administrator to configure LiveKit."
                )
            ),
            lifecycle_sync_url=lifecycle_sync_url,
            end_url=end_url if role == VideoParticipantRole.INTERVIEWER else None,
            join_allowed=join_allowed,
            started_at=interview.started_at,
            ended_at=interview.ended_at,
        )

    def _get_candidate_interview(self, invitation_token: str) -> InterviewSession:
        if not invitation_token or len(invitation_token) > 512:
            raise InterviewNotFoundError
        interview = self._interviews.get_by_token_hash(hash_token(invitation_token))
        if interview is None:
            raise InterviewNotFoundError
        return interview

    def _get_interview(self, interview_id: UUID) -> InterviewSession:
        interview = self._interviews.get(interview_id)
        if interview is None:
            raise InterviewNotFoundError
        return interview

    def _get_interview_for_update(self, interview_id: UUID) -> InterviewSession:
        interview = self._interviews.get_for_update(interview_id)
        if interview is None:
            raise InterviewNotFoundError
        return interview

    def _get_application(self, interview: InterviewSession) -> JobApplication:
        application = self._interviews.get_application(interview.application_id)
        if (
            application is None
            or application.job_id != interview.job_id
            or application.candidate_id != interview.candidate_id
        ):
            raise InterviewSchedulingError("Interview relationships are invalid.")
        return application
