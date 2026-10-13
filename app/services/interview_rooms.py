import base64
import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from app.errors import (
    InterviewConfigurationError,
    InterviewNotFoundError,
    InterviewSchedulingError,
    JobNotFoundError,
)
from app.models.applications import JobApplication
from app.models.interviews import InterviewSession
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.schemas.interview_signaling import (
    InterviewParticipantRole,
    SignalingTicketClaims,
)
from app.schemas.interviews import IceServerConfiguration, InterviewRoomView
from app.services.interview_scheduling import ensure_utc, hash_token

SIGNALING_TICKET_CONTEXT = b"recrunion-interview-signaling-v1:"
MAX_SIGNALING_TICKET_LENGTH = 4096


class SignalingTicketError(ValueError):
    """Represent an invalid, expired, or incorrectly scoped signalling ticket."""


class SignalingTicketCodec:
    """Issue and verify short-lived, HMAC-protected signalling tickets."""

    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret
        self._ttl_seconds = ttl_seconds

    def issue(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        *,
        now: datetime | None = None,
    ) -> str:
        self._validate_configuration()
        issued_at = int((now or datetime.now(UTC)).timestamp())
        claims = SignalingTicketClaims(
            interview_id=interview_id,
            role=role,
            issued_at=issued_at,
            expires_at=issued_at + self._ttl_seconds,
        )
        payload = claims.model_dump_json().encode("utf-8")
        encoded_payload = _encode_base64(payload)
        signature = self._signature(encoded_payload)
        return f"{encoded_payload}.{_encode_base64(signature)}"

    def verify(
        self,
        ticket: str,
        *,
        expected_interview_id: UUID,
        expected_role: InterviewParticipantRole | None = None,
        now: datetime | None = None,
    ) -> SignalingTicketClaims:
        self._validate_configuration()
        if not ticket or len(ticket) > MAX_SIGNALING_TICKET_LENGTH:
            raise SignalingTicketError("The signalling ticket is invalid.")
        try:
            encoded_payload, encoded_signature = ticket.split(".", maxsplit=1)
            supplied_signature = _decode_base64(encoded_signature)
            expected_signature = self._signature(encoded_payload)
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise SignalingTicketError("The signalling ticket is invalid.")
            claims = SignalingTicketClaims.model_validate_json(_decode_base64(encoded_payload))
        except (ValueError, UnicodeDecodeError, ValidationError) as error:
            raise SignalingTicketError("The signalling ticket is invalid.") from error

        current_timestamp = int((now or datetime.now(UTC)).timestamp())
        if claims.expires_at <= current_timestamp:
            raise SignalingTicketError("The signalling ticket has expired.")
        if claims.issued_at > current_timestamp + 30:
            raise SignalingTicketError("The signalling ticket is invalid.")
        if claims.interview_id != expected_interview_id:
            raise SignalingTicketError("The signalling ticket has the wrong interview scope.")
        if expected_role is not None and claims.role != expected_role:
            raise SignalingTicketError("The signalling ticket has the wrong participant role.")
        return claims

    def _signature(self, encoded_payload: str) -> bytes:
        return hmac.new(
            self._secret.encode("utf-8"),
            SIGNALING_TICKET_CONTEXT + encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()

    def _validate_configuration(self) -> None:
        if len(self._secret) < 32 or not 30 <= self._ttl_seconds <= 3600:
            raise InterviewConfigurationError("Secure interview signalling is not configured.")


class InterviewRoomService:
    """Authorize interview-room entry and issue scoped signalling tickets."""

    def __init__(
        self,
        interview_repository: InterviewRepository,
        job_repository: JobRepository,
        *,
        token_secret: str,
        signaling_ticket_ttl_seconds: int,
        stun_urls: list[str],
    ) -> None:
        self._interviews = interview_repository
        self._jobs = job_repository
        self._tickets = SignalingTicketCodec(
            token_secret,
            signaling_ticket_ttl_seconds,
        )
        self._ice_servers = [IceServerConfiguration(urls=stun_urls)] if stun_urls else []

    def get_candidate_room(self, invitation_token: str) -> InterviewRoomView:
        if not invitation_token or len(invitation_token) > 512:
            raise InterviewNotFoundError
        interview = self._interviews.get_by_token_hash(hash_token(invitation_token))
        if interview is None:
            raise InterviewNotFoundError
        return self._room_view(interview, InterviewParticipantRole.CANDIDATE)

    def get_interviewer_room(self, interview_id: UUID) -> InterviewRoomView:
        interview = self._get_interview(interview_id)
        return self._room_view(interview, InterviewParticipantRole.INTERVIEWER)

    def authenticate_ticket(
        self,
        ticket: str,
        expected_interview_id: UUID,
    ) -> SignalingTicketClaims:
        claims = self._tickets.verify(
            ticket,
            expected_interview_id=expected_interview_id,
        )
        self._get_interview(claims.interview_id)
        return claims

    def issue_ticket(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        *,
        now: datetime | None = None,
    ) -> str:
        self._get_interview(interview_id)
        return self._tickets.issue(interview_id, role, now=now)

    def _room_view(
        self,
        interview: InterviewSession,
        role: InterviewParticipantRole,
    ) -> InterviewRoomView:
        job = self._jobs.get(interview.job_id)
        if job is None:
            raise JobNotFoundError(interview.job_id)
        application = self._get_application(interview)
        return InterviewRoomView(
            interview_id=interview.id,
            job_title=job.title,
            candidate_reference=(
                application.candidate.display_reference
                if role == InterviewParticipantRole.INTERVIEWER
                else None
            ),
            role=role,
            status=interview.status,
            scheduled_start_at=ensure_utc(interview.scheduled_start_at),
            duration_minutes=interview.duration_minutes,
            timezone=interview.display_timezone,
            signaling_ticket=self._tickets.issue(interview.id, role),
            ice_servers=self._ice_servers,
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


def parse_stun_urls(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _encode_base64(decoded) != value:
        raise ValueError("Non-canonical base64 encoding")
    return decoded
