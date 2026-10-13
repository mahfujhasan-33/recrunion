import base64
import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError

from app.adapters.email import EmailAdapter, OutboundEmail
from app.errors import (
    ApplicationNotFoundError,
    GmailConfigurationError,
    GmailError,
    InterviewAlreadyScheduledError,
    InterviewConfigurationError,
    InterviewNotFoundError,
    InterviewSchedulingError,
    JobNotFoundError,
)
from app.models.applications import JobApplication
from app.models.interviews import InterviewSession, InterviewStatus, InvitationStatus
from app.models.jobs import Job
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.schemas.candidate_profiles import CandidateProfileData, ContactDetailKind
from app.schemas.interviews import (
    CandidateInterviewView,
    InterviewResponse,
    InterviewScheduleRequest,
    InterviewSchedulingDefaults,
    InvitationEmailSource,
)

TOKEN_CONTEXT = b"recrunion-candidate-interview-v1:"
EMAIL_ADAPTER = TypeAdapter(EmailStr)


class InterviewSchedulingService:
    """Apply human-triggered scheduling rules and coordinate invitation delivery."""

    def __init__(
        self,
        interview_repository: InterviewRepository,
        job_repository: JobRepository,
        email_adapter: EmailAdapter,
        *,
        app_base_url: str,
        token_secret: str,
    ) -> None:
        self._interviews = interview_repository
        self._jobs = job_repository
        self._email = email_adapter
        self._app_base_url = app_base_url.rstrip("/")
        self._token_secret = token_secret

    async def schedule(
        self,
        application_id: UUID,
        payload: InterviewScheduleRequest,
    ) -> InterviewResponse:
        self._validate_token_configuration()
        application = self._interviews.get_application(application_id)
        if application is None:
            raise ApplicationNotFoundError
        job = self._get_job(application.job_id)
        if application.candidate.id != application.candidate_id:
            raise InterviewSchedulingError("Candidate and application records do not match.")
        if self._interviews.get_score(application.id) is None:
            raise InterviewSchedulingError(
                "The recruiter must review a persisted suitability score before scheduling."
            )
        if self._interviews.get_for_application(application.id) is not None:
            raise InterviewAlreadyScheduledError(
                "This application already has a scheduled interview."
            )
        scheduled_start_at = normalize_schedule(payload)
        interview_id = uuid4()
        token = self._token_for(interview_id)
        interview = InterviewSession(
            id=interview_id,
            job_id=job.id,
            application_id=application.id,
            candidate_id=application.candidate_id,
            status=InterviewStatus.SCHEDULED,
            scheduled_start_at=scheduled_start_at,
            duration_minutes=payload.duration_minutes,
            display_timezone=payload.timezone,
            candidate_access_token_hash=hash_token(token),
            invitation_email=str(payload.invitation_email),
            invitation_status=InvitationStatus.PENDING,
        )
        try:
            self._interviews.save(interview)
        except IntegrityError as error:
            self._interviews.rollback()
            raise InterviewAlreadyScheduledError(
                "This application already has a scheduled interview."
            ) from error
        await self._send_invitation(interview, job, token)
        return self._response(interview, job, application)

    async def resend_invitation(self, interview_id: UUID) -> InterviewResponse:
        self._validate_token_configuration()
        interview = self._get_interview(interview_id)
        job = self._get_job(interview.job_id)
        application = self._get_application(interview)
        token = self._token_for(interview.id)
        current_token_hash = hash_token(token)
        if not hmac.compare_digest(
            current_token_hash,
            interview.candidate_access_token_hash,
        ):
            interview.candidate_access_token_hash = current_token_hash
            self._interviews.save(interview)
        await self._send_invitation(interview, job, token)
        return self._response(interview, job, application)

    def get_scheduling_defaults(self, application_id: UUID) -> InterviewSchedulingDefaults:
        application = self._interviews.get_application(application_id)
        if application is None:
            raise ApplicationNotFoundError
        email, source = self._default_invitation_email(application)
        return InterviewSchedulingDefaults(
            application_id=application.id,
            candidate_reference=application.candidate.display_reference,
            invitation_email=email,
            email_source=source,
        )

    def get_interview(self, interview_id: UUID) -> InterviewResponse:
        interview = self._get_interview(interview_id)
        job = self._get_job(interview.job_id)
        return self._response(interview, job, self._get_application(interview))

    def list_for_job(self, job_id: UUID) -> list[InterviewResponse]:
        job = self._get_job(job_id)
        return [
            self._response(interview, job, self._get_application(interview))
            for interview in self._interviews.list_for_job(job_id)
        ]

    def get_candidate_view(self, token: str) -> CandidateInterviewView:
        if not token or len(token) > 512:
            raise InterviewNotFoundError
        interview = self._interviews.get_by_token_hash(hash_token(token))
        if interview is None:
            raise InterviewNotFoundError
        job = self._get_job(interview.job_id)
        return CandidateInterviewView(
            job_title=job.title,
            status=interview.status,
            scheduled_start_at=ensure_utc(interview.scheduled_start_at),
            duration_minutes=interview.duration_minutes,
            timezone=interview.display_timezone,
        )

    async def _send_invitation(
        self,
        interview: InterviewSession,
        job: Job,
        token: str,
    ) -> None:
        if interview.invitation_email is None:
            raise InterviewSchedulingError(
                "This interview does not have a persisted invitation email."
            )
        interview.invitation_status = InvitationStatus.PENDING
        interview.invitation_error_code = None
        interview.invitation_error_message = None
        self._interviews.save(interview)
        try:
            result = await self._email.send(
                build_invitation(
                    job,
                    interview,
                    self._join_url(token),
                )
            )
        except GmailConfigurationError as error:
            interview.invitation_status = InvitationStatus.NOT_CONFIGURED
            interview.invitation_error_code = error.code
            interview.invitation_error_message = str(error)
        except GmailError as error:
            interview.invitation_status = InvitationStatus.FAILED
            interview.invitation_error_code = error.code
            interview.invitation_error_message = str(error)
        else:
            interview.invitation_status = InvitationStatus.SENT
            interview.invitation_provider_message_id = result.provider_message_id
            interview.invitation_sent_at = result.sent_at
        self._interviews.save(interview)

    def _response(
        self,
        interview: InterviewSession,
        job: Job,
        application: JobApplication,
    ) -> InterviewResponse:
        candidate_join_url = self._candidate_join_url(interview)
        return InterviewResponse(
            id=interview.id,
            job_id=interview.job_id,
            application_id=interview.application_id,
            candidate_id=interview.candidate_id,
            candidate_reference=application.candidate.display_reference,
            job_title=job.title,
            status=interview.status,
            scheduled_start_at=ensure_utc(interview.scheduled_start_at),
            duration_minutes=interview.duration_minutes,
            timezone=interview.display_timezone,
            invitation_status=interview.invitation_status,
            invitation_email=interview.invitation_email,
            invitation_sent_at=(
                ensure_utc(interview.invitation_sent_at)
                if interview.invitation_sent_at is not None
                else None
            ),
            invitation_error_code=interview.invitation_error_code,
            invitation_error_message=interview.invitation_error_message,
            candidate_join_url=candidate_join_url,
            started_at=(
                ensure_utc(interview.started_at) if interview.started_at is not None else None
            ),
            ended_at=(ensure_utc(interview.ended_at) if interview.ended_at is not None else None),
            created_at=ensure_utc(interview.created_at),
            updated_at=ensure_utc(interview.updated_at),
        )

    def _get_interview(self, interview_id: UUID) -> InterviewSession:
        interview = self._interviews.get(interview_id)
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

    def _get_job(self, job_id: UUID) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    @staticmethod
    def _default_invitation_email(
        application: JobApplication,
    ) -> tuple[str | None, InvitationEmailSource]:
        contact_email = InterviewSchedulingService._valid_email(application.contact_email)
        if contact_email is not None:
            return contact_email, InvitationEmailSource.APPLICATION_EMAIL
        profile = application.document.profile if application.document is not None else None
        if profile is None:
            return None, InvitationEmailSource.NOT_AVAILABLE
        try:
            data = CandidateProfileData.model_validate(profile.structured_json)
            email = next(
                (
                    valid_email
                    for item in data.contact_details
                    if item.kind == ContactDetailKind.EMAIL
                    and (valid_email := InterviewSchedulingService._valid_email(item.value))
                    is not None
                ),
                None,
            )
        except ValidationError:
            return None, InvitationEmailSource.NOT_AVAILABLE
        if email is None:
            return None, InvitationEmailSource.NOT_AVAILABLE
        return email, InvitationEmailSource.CV_EXTRACTED_EMAIL

    @staticmethod
    def _valid_email(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(EMAIL_ADAPTER.validate_python(value.strip()))
        except ValidationError:
            return None

    def _validate_token_configuration(self) -> None:
        if len(self._token_secret) < 32 or not self._app_base_url:
            raise InterviewConfigurationError(
                "Candidate interview links are not securely configured."
            )

    def _token_for(self, interview_id: UUID) -> str:
        digest = hmac.new(
            self._token_secret.encode("utf-8"),
            TOKEN_CONTEXT + interview_id.bytes,
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def _verified_token(self, interview: InterviewSession) -> str:
        self._validate_token_configuration()
        token = self._token_for(interview.id)
        if not hmac.compare_digest(hash_token(token), interview.candidate_access_token_hash):
            raise InterviewConfigurationError(
                "The configured candidate-link secret does not match this interview."
            )
        return token

    def _candidate_join_url(self, interview: InterviewSession) -> str | None:
        try:
            token = self._verified_token(interview)
        except InterviewConfigurationError:
            return None
        return self._join_url(token)

    def _join_url(self, token: str) -> str:
        return f"{self._app_base_url}/interviews/join/{token}"


def normalize_schedule(payload: InterviewScheduleRequest) -> datetime:
    local_start = payload.scheduled_start_local
    if local_start.tzinfo is not None:
        raise InterviewSchedulingError(
            "Use a local date and time without a UTC offset, together with the timezone field."
        )
    try:
        timezone = ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as error:
        raise InterviewSchedulingError("The selected timezone is not recognized.") from error
    fold_zero = local_start.replace(tzinfo=timezone, fold=0)
    fold_one = local_start.replace(tzinfo=timezone, fold=1)
    if fold_zero.utcoffset() != fold_one.utcoffset():
        raise InterviewSchedulingError(
            "The selected local time is ambiguous because of a daylight-saving transition."
        )
    scheduled_utc = fold_zero.astimezone(UTC)
    round_trip = scheduled_utc.astimezone(timezone).replace(tzinfo=None)
    if round_trip != local_start:
        raise InterviewSchedulingError(
            "The selected local time does not exist because of a daylight-saving transition."
        )
    if scheduled_utc <= datetime.now(UTC):
        raise InterviewSchedulingError("Interview time must be in the future.")
    return scheduled_utc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_invitation(
    job: Job,
    interview: InterviewSession,
    join_url: str,
) -> OutboundEmail:
    if interview.invitation_email is None:
        raise InterviewSchedulingError("This interview does not have a persisted invitation email.")
    local_start = ensure_utc(interview.scheduled_start_at).astimezone(
        ZoneInfo(interview.display_timezone)
    )
    formatted_start = local_start.strftime("%A, %d %B %Y at %I:%M %p")
    body = (
        "You are invited to a RecrUnion interview.\n\n"
        f"Position: {job.title}\n"
        f"Scheduled: {formatted_start}\n"
        f"Timezone: {interview.display_timezone}\n"
        f"Duration: {interview.duration_minutes} minutes\n\n"
        f"Secure interview link: {join_url}\n\n"
        "Open the link at the scheduled time. Keep it private because it provides access "
        "to your interview session.\n"
    )
    return OutboundEmail(
        recipient=interview.invitation_email,
        subject=f"RecrUnion interview invitation — {job.title}",
        text_body=body,
    )
