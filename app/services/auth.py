import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

from app.errors import AuthConfigurationError, AuthenticationError, AuthorizationError
from app.models.auth import RecruiterSession, User
from app.repositories.auth import AuthRepository

PASSWORD_ITERATIONS = 600_000
TOKEN_ISSUER = "recrunion"
TOKEN_AUDIENCE = "recrunion-recruiter"
RECRUITER_ROLES = {"RECRUITER", "INTERVIEWER"}


def hash_password(password: str) -> str:
    """Use a random salt and PBKDF2-HMAC-SHA256 without an extra password SDK."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str | None) -> bool:
    # Unknown users still pay the same password-derivation cost.
    if encoded is None:
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"\x00" * 16, PASSWORD_ITERATIONS)
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != PASSWORD_ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class RecruiterPrincipal:
    user_id: UUID
    username: str
    role: str
    session_id: UUID
    csrf_token: str


class AuthService:
    """Authenticate recruiters only; candidate and service credentials are separate."""

    def __init__(self, repository: AuthRepository, *, secret: str, token_minutes: int) -> None:
        self._repository = repository
        self._secret = secret
        self._token_minutes = token_minutes

    def ensure_configured(self) -> None:
        if len(self._secret.encode()) < 32 or self._secret.startswith("CHANGE_ME"):
            raise AuthConfigurationError("Recruiter authentication is not configured.")

    def bootstrap_recruiter(self, username: str, password: str) -> bool:
        self.ensure_configured()
        username = username.strip().casefold()
        if not username or len(username) > 254 or not 12 <= len(password) <= 128:
            raise AuthConfigurationError(
                "Set a recruiter username and a password between 12 and 128 characters."
            )
        if self._repository.get_user_by_username(username) is not None:
            return False
        if self._repository.has_users():
            raise AuthConfigurationError("An initial account already exists. Bootstrap is closed.")
        self._repository.save_user(
            User(username=username, password_hash=hash_password(password), role="RECRUITER")
        )
        return True

    def login(self, username: str, password: str) -> str:
        self.ensure_configured()
        if len(username) > 254 or len(password) > 128:
            raise AuthenticationError("The username or password is incorrect.")
        user = self._repository.get_user_by_username(username.strip().casefold())
        correct = verify_password(password, user.password_hash if user else None)
        if not correct or user is None or not user.is_active or user.role not in RECRUITER_ROLES:
            raise AuthenticationError("The username or password is incorrect.")
        now = datetime.now(UTC)
        session = RecruiterSession(
            id=uuid4(), user_id=user.id, expires_at=now + timedelta(minutes=self._token_minutes)
        )
        token = jwt.encode(
            {
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
                "sub": str(user.id),
                "jti": str(session.id),
                "iat": now,
                "exp": session.expires_at,
                "csrf": secrets.token_urlsafe(32),
            },
            self._secret,
            algorithm="HS256",
        )
        self._repository.create_session(session, now)
        return token

    def authenticate(self, token: str | None) -> RecruiterPrincipal:
        if not token:
            raise AuthenticationError("Sign in to access recruiter functionality.")
        self.ensure_configured()
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=TOKEN_AUDIENCE,
                issuer=TOKEN_ISSUER,
                options={"require": ["sub", "jti", "iat", "exp", "csrf"]},
            )
            user_id, session_id = UUID(claims["sub"]), UUID(claims["jti"])
            csrf = claims["csrf"]
            if not isinstance(csrf, str) or len(csrf) < 32:
                raise ValueError("Invalid CSRF claim")
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as error:
            raise AuthenticationError(
                "Your recruiter session has expired. Sign in again."
            ) from error
        session = self._repository.get_session(session_id)
        if (
            session is None
            or session.user_id != user_id
            or session.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
        ):
            raise AuthenticationError("Your recruiter session has expired. Sign in again.")
        user = self._repository.get_user(user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Your recruiter session has expired. Sign in again.")
        if user.role not in RECRUITER_ROLES:
            raise AuthorizationError("This account cannot access recruiter functionality.")
        return RecruiterPrincipal(user.id, user.username, user.role, session.id, csrf)

    def logout(self, principal: RecruiterPrincipal) -> None:
        self._repository.revoke_session(principal.session_id)
