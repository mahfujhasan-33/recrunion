import base64
import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from app.errors import (
    InterviewConfigurationError,
    InterviewEndedError,
    InterviewLifecycleError,
    InterviewNotFoundError,
    InterviewSchedulingError,
    JobNotFoundError,
)
from app.models.applications import JobApplication
from app.models.interviews import InterviewSession, InterviewStatus
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
        turn_urls: list[str] | None = None,
        turn_username: str = "",
        turn_password: str = "",
        ice_transport_policy: str = "all",
    ) -> None:
        self._interviews = interview_repository
        self._jobs = job_repository
        self._tickets = SignalingTicketCodec(
            token_secret,
            signaling_ticket_ttl_seconds,
        )
        self._ice_servers = build_ice_server_configuration(
            stun_urls,
            turn_urls or [],
            turn_username,
            turn_password,
            ice_transport_policy,
        )
        self._ice_transport_policy = ice_transport_policy

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
        interview = self._get_interview(claims.interview_id)
        self._ensure_joinable(interview)
        return claims

    def issue_ticket(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        *,
        now: datetime | None = None,
    ) -> str:
        interview = self._get_interview(interview_id)
        self._ensure_joinable(interview)
        return self._tickets.issue(interview_id, role, now=now)

    def participant_joined(self, interview_id: UUID) -> InterviewStatus:
        """Persist the first authorized room entry without starting the call."""

        interview = self._get_interview_for_update(interview_id)
        self._ensure_joinable(interview)
        if interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.WAITING
            self._interviews.save(interview)
        return interview.status

    def mark_active(
        self,
        interview_id: UUID,
        *,
        now: datetime | None = None,
    ) -> InterviewSession:
        """Start an interview after both authenticated peers confirm connection."""

        interview = self._get_interview_for_update(interview_id)
        self._ensure_joinable(interview)
        if interview.status == InterviewStatus.SCHEDULED:
            raise InterviewLifecycleError("Participants must join before the interview can start.")
        if interview.status == InterviewStatus.WAITING:
            interview.status = InterviewStatus.ACTIVE
            if interview.started_at is None:
                interview.started_at = now or datetime.now(UTC)
            self._interviews.save(interview)
        return interview

    def end_interview(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        *,
        now: datetime | None = None,
    ) -> InterviewSession:
        """Persist the interviewer-controlled end of a joined interview."""

        if role != InterviewParticipantRole.INTERVIEWER:
            raise InterviewLifecycleError("Only the interviewer can end the interview.")
        interview = self._get_interview_for_update(interview_id)
        if interview.status == InterviewStatus.ENDED:
            return interview
        if interview.status == InterviewStatus.SCHEDULED:
            raise InterviewLifecycleError("The interview has not been joined yet.")
        interview.status = InterviewStatus.ENDED
        if interview.ended_at is None:
            interview.ended_at = now or datetime.now(UTC)
        return self._interviews.save(interview)

    def _room_view(
        self,
        interview: InterviewSession,
        role: InterviewParticipantRole,
    ) -> InterviewRoomView:
        job = self._jobs.get(interview.job_id)
        if job is None:
            raise JobNotFoundError(interview.job_id)
        application = self._get_application(interview)
        join_allowed = interview.status != InterviewStatus.ENDED
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
            signaling_ticket=(self._tickets.issue(interview.id, role) if join_allowed else None),
            ice_servers=self._ice_servers if join_allowed else [],
            ice_transport_policy=self._ice_transport_policy,
            join_allowed=join_allowed,
            started_at=interview.started_at,
            ended_at=interview.ended_at,
        )

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

    @staticmethod
    def _ensure_joinable(interview: InterviewSession) -> None:
        if interview.status == InterviewStatus.ENDED:
            raise InterviewEndedError

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
    return parse_ice_urls(value, allowed_schemes=("stun:", "stuns:"))


def parse_turn_urls(value: str) -> list[str]:
    return parse_ice_urls(value, allowed_schemes=("turn:", "turns:"))


def parse_ice_urls(value: str, *, allowed_schemes: tuple[str, ...]) -> list[str]:
    urls = [item.strip() for item in value.split(",") if item.strip()]
    if any(not url.casefold().startswith(allowed_schemes) for url in urls):
        raise InterviewConfigurationError("The WebRTC ICE server configuration is invalid.")
    return urls


def build_ice_server_configuration(
    stun_urls: list[str],
    turn_urls: list[str],
    turn_username: str,
    turn_password: str,
    ice_transport_policy: str,
) -> list[IceServerConfiguration]:
    """Normalize browser ICE configuration and reject incomplete TURN settings."""

    if ice_transport_policy not in {"all", "relay"}:
        raise InterviewConfigurationError("The WebRTC ICE transport policy is invalid.")
    has_turn_credentials = bool(turn_username) and bool(turn_password)
    if bool(turn_username) != bool(turn_password):
        raise InterviewConfigurationError("TURN credentials are incomplete.")
    if turn_urls and not has_turn_credentials:
        raise InterviewConfigurationError("TURN credentials are not configured.")
    if has_turn_credentials and not turn_urls:
        raise InterviewConfigurationError("A TURN URL is not configured.")
    if ice_transport_policy == "relay" and not turn_urls:
        raise InterviewConfigurationError("Relay-only WebRTC requires a TURN server.")

    servers: list[IceServerConfiguration] = []
    if stun_urls:
        servers.append(IceServerConfiguration(urls=stun_urls))
    if turn_urls:
        servers.append(
            IceServerConfiguration(
                urls=turn_urls,
                username=turn_username,
                credential=turn_password,
            )
        )
    return servers


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _encode_base64(decoded) != value:
        raise ValueError("Non-canonical base64 encoding")
    return decoded
