import hmac
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db_session
from app.errors import AuthorizationError
from app.repositories.auth import AuthRepository
from app.services.auth import AuthService, RecruiterPrincipal

SESSION_COOKIE = "recrunion_session"
LOGIN_CSRF_COOKIE = "recrunion_login_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_auth_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(
        AuthRepository(session),
        secret=settings.jwt_secret,
        token_minutes=settings.access_token_minutes,
    )


def get_current_recruiter(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> RecruiterPrincipal:
    principal = service.authenticate(request.cookies.get(SESSION_COOKIE))
    request.state.recruiter = principal
    request.state.csrf_token = principal.csrf_token
    return principal


def validate_request_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("Origin")
    configured = urlsplit(settings.app_base_url)
    allowed = {
        f"{request.url.scheme}://{request.url.netloc}",
        f"{configured.scheme}://{configured.netloc}",
    }
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and origin not in allowed):
        raise AuthorizationError("This request origin is not allowed.")


async def require_recruiter(
    request: Request,
    principal: Annotated[RecruiterPrincipal, Depends(get_current_recruiter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RecruiterPrincipal:
    if request.method not in SAFE_METHODS:
        validate_request_origin(request, settings)
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied and request.headers.get("content-type", "").split(";")[0] == (
            "application/x-www-form-urlencoded"
        ):
            form = await request.form()
            supplied = str(form.get("csrf_token", ""))
        if not supplied or not hmac.compare_digest(
            supplied.encode(), principal.csrf_token.encode()
        ):
            raise AuthorizationError(
                "The security token is missing or expired. Refresh and try again."
            )
    return principal


def secure_cookie(request: Request, settings: Settings) -> bool:
    return (
        settings.auth_cookie_secure
        or request.url.scheme == "https"
        or urlsplit(settings.app_base_url).scheme == "https"
    )


def safe_next_path(value: str) -> str:
    # Only a plain local path is accepted; no URL, encoded separators, or query credentials.
    if (
        not value.startswith("/")
        or value.startswith("//")
        or len(value) > 2048
        or any(char in value for char in ("\\", "%", "?", "#"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return "/"
    return value
