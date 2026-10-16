import hmac
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    get_auth_service,
    require_recruiter,
    safe_next_path,
    secure_cookie,
    validate_request_origin,
)
from app.config import Settings, get_settings
from app.errors import AuthenticationError, AuthorizationError
from app.services.auth import AuthService, RecruiterPrincipal

router = APIRouter(tags=["recruiter authentication"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def login_response(
    request: Request,
    settings: Settings,
    *,
    next_path: str,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    csrf_token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"login_csrf": csrf_token, "next_path": safe_next_path(next_path), "error": error},
        status_code=status_code,
    )
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        csrf_token,
        max_age=600,
        httponly=True,
        secure=secure_cookie(request, settings),
        samesite="strict",
        path="/login",
    )
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, settings: SettingsDependency, next: str = "/") -> HTMLResponse:
    return login_response(request, settings, next_path=next)


@router.post("/login", response_class=HTMLResponse, response_model=None)
def login(
    request: Request,
    service: AuthServiceDependency,
    settings: SettingsDependency,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form(max_length=128)] = "",
    next: Annotated[str, Form(max_length=2048)] = "/",
) -> HTMLResponse | RedirectResponse:
    validate_request_origin(request, settings)
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    if (
        not csrf_token
        or not cookie_token
        or not hmac.compare_digest(csrf_token.encode(), cookie_token.encode())
    ):
        raise AuthorizationError("The login form has expired. Reload it and try again.")
    try:
        token = service.login(username, password)
    except AuthenticationError:
        return login_response(
            request,
            settings,
            next_path=next,
            error="The username or password is incorrect.",
            status_code=401,
        )
    response = RedirectResponse(safe_next_path(next), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=secure_cookie(request, settings),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
    return response


@router.post("/logout")
def logout(
    request: Request,
    principal: Annotated[RecruiterPrincipal, Depends(require_recruiter)],
    service: AuthServiceDependency,
    settings: SettingsDependency,
) -> RedirectResponse:
    service.logout(principal)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=secure_cookie(request, settings),
        httponly=True,
        samesite="lax",
    )
    return response
