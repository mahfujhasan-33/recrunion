import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, require_recruiter
from app.config import Settings, get_settings
from app.dependencies import get_transcript_service
from app.errors import AuthConfigurationError
from app.models.auth import RecruiterSession, User
from app.repositories.auth import AuthRepository
from app.services.auth import AuthService, verify_password
from tests.test_interview_livekit import create_interview
from tests.test_transcripts import build_service, segment_payload

TEST_SECRET = "synthetic-recruiter-signing-secret-at-least-32-characters"
TEST_PASSWORD = "synthetic-recruiter-password"


def login(
    client: TestClient,
    *,
    password: str = TEST_PASSWORD,
    next_path: str = "/",
    username: str = "recruiter@example.test",
):
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": csrf,
            "next": next_path,
        },
        follow_redirects=False,
    )


def test_login_refresh_logout_revokes_replayed_cookie(application: FastAPI) -> None:
    with TestClient(application) as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/api/v1/jobs").status_code == 401
        assert login(client, password="wrong-password").status_code == 401
        assert login(client).status_code == 303
        token = client.cookies.get(SESSION_COOKIE)
        for _ in range(2):
            home = client.get("/")
            assert home.status_code == 200
            assert "recruiter@example.test" in home.text
            assert 'action="/logout"' in home.text
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text).group(1)
        assert client.post("/logout", follow_redirects=False).status_code == 403
        logout = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
        assert logout.status_code == 303
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/v1/jobs").status_code == 401


@pytest.mark.parametrize("https", [False, True])
def test_login_cookie_security(application: FastAPI, https: bool) -> None:
    origin = "https://testserver" if https else "http://testserver"
    application.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, jwt_secret=TEST_SECRET, app_base_url=origin
    )
    with TestClient(application, base_url=origin) as client:
        response = login(client)
        cookie = response.headers["set-cookie"].split(", recrunion_login_csrf", 1)[0]
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Path=/" in cookie
        assert "Max-Age=3600" in cookie
        assert ("; Secure" in cookie) is https
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/jobs").status_code == 200


@pytest.mark.parametrize("credential", [None, "candidate-capability", "internal-stt-secret"])
def test_every_sensitive_route_rejects_non_recruiter_credentials(
    application: FastAPI, credential: str | None
) -> None:
    public_paths = {
        "/login",
        "/health",
        "/ready",
        "/interviews/join/{token}",
        "/interviews/join/{token}/lifecycle-sync",
        "/api/v1/internal/interviews/{interview_id}/transcript-segments",
    }
    families = set()
    with TestClient(application) as client:
        if credential:
            client.cookies.set(SESSION_COOKIE, credential)
            client.headers["Authorization"] = f"Bearer {credential}"
        for route in application.routes:
            if not isinstance(route, APIRoute) or route.path in public_paths:
                continue
            assert any(
                dependency.call in {require_recruiter}
                for dependency in route.dependant.dependencies
            ), route.path
            path = re.sub(r"\{[^}]+\}", str(UUID(int=1)), route.path)
            families.update(route.tags)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                response = client.request(method, path, follow_redirects=False)
                assert response.status_code == (401 if path.startswith("/api/") else 303), (
                    method,
                    route.path,
                    response.status_code,
                )
        assert {
            "jobs",
            "applications",
            "candidate screening",
            "candidate scoring",
            "job publications",
            "company documents",
            "recruiter assistant",
            "processing tasks",
            "interviews",
            "interview transcripts",
            "email intake",
        } <= families


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutations_require_session_csrf_and_same_origin(
    application: FastAPI, client: TestClient, method: str
) -> None:
    @application.api_route(
        "/api/v1/auth-test-mutation", methods=[method], dependencies=[Depends(require_recruiter)]
    )
    def mutation() -> dict[str, bool]:
        return {"ok": True}

    csrf = client.headers.pop("X-CSRF-Token")
    assert client.request(method, "/api/v1/auth-test-mutation").status_code == 403
    assert (
        client.request(
            method, "/api/v1/auth-test-mutation", headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.request(
            method,
            "/api/v1/auth-test-mutation",
            headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert (
        client.request(
            method,
            "/api/v1/auth-test-mutation",
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        ).status_code
        == 200
    )


def test_login_requires_csrf_and_rejects_foreign_origin(application: FastAPI) -> None:
    with TestClient(application) as client:
        credentials = {"username": "recruiter@example.test", "password": TEST_PASSWORD}
        assert client.post("/login", data=credentials).status_code == 403
        page = client.get("/login")
        credentials["csrf_token"] = re.search(
            r'name="csrf_token" value="([^"]+)"', page.text
        ).group(1)
        assert (
            client.post(
                "/login", data=credentials, headers={"Origin": "https://attacker.example"}
            ).status_code
            == 403
        )
        credentials["csrf_token"] = "invalid"
        assert client.post("/login", data=credentials).status_code == 403


def test_native_forms_keep_origin_and_candidate_links_suppress_referrers(
    application: FastAPI, db_session: Session
) -> None:
    create_interview(db_session, invitation_token="synthetic-referrer-test-link")
    with TestClient(application) as client:
        page = client.get("/login")
        assert page.headers["referrer-policy"] == "same-origin"
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        credentials = {
            "username": "recruiter@example.test",
            "password": TEST_PASSWORD,
            "csrf_token": csrf,
        }
        # Opaque and unrelated origins remain blocked; do not bypass the origin guard.
        for origin in ("null", "https://attacker.example"):
            assert (
                client.post("/login", data=credentials, headers={"Origin": origin}).status_code
                == 403
            )
        assert (
            client.post(
                "/login",
                data=credentials,
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        home = client.get("/")
        assert home.headers["referrer-policy"] == "same-origin"
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text).group(1)
        assert (
            client.post(
                "/logout",
                data={"csrf_token": csrf},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            ).status_code
            == 303
        )
    with TestClient(application) as candidate:
        page = candidate.get("/interviews/join/synthetic-referrer-test-link")
        assert page.status_code == 200
        assert page.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "destination", ["https://evil.example", "//evil.example", "/\\evil", "/%2f%2fevil"]
)
def test_login_rejects_open_redirects(application: FastAPI, destination: str) -> None:
    with TestClient(application) as client:
        response = login(client, next_path=destination)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_password_hash_and_one_time_bootstrap(db_session: Session) -> None:
    service = AuthService(AuthRepository(db_session), secret=TEST_SECRET, token_minutes=60)
    assert service.bootstrap_recruiter("initial@example.test", TEST_PASSWORD) is True
    user = db_session.scalar(select(User))
    assert user.password_hash != TEST_PASSWORD
    assert verify_password(TEST_PASSWORD, user.password_hash)
    assert not verify_password("wrong", user.password_hash)
    original_hash = user.password_hash
    assert service.bootstrap_recruiter("initial@example.test", "another-valid-password") is False
    assert user.password_hash == original_hash
    with pytest.raises(AuthConfigurationError):
        service.bootstrap_recruiter("another@example.test", TEST_PASSWORD)


@pytest.mark.parametrize("state", ["expired-session", "expired-jwt", "inactive-user", "wrong-role"])
def test_invalid_session_or_account_is_rejected(
    application: FastAPI, db_session: Session, state: str
) -> None:
    with TestClient(application) as client:
        assert login(client).status_code == 303
        if state == "expired-session":
            session = db_session.scalar(select(RecruiterSession))
            session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        elif state == "expired-jwt":
            claims = jwt.decode(
                client.cookies.get(SESSION_COOKIE),
                TEST_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            claims["exp"] = datetime.now(UTC) - timedelta(minutes=1)
            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE, jwt.encode(claims, TEST_SECRET, algorithm="HS256"))
        else:
            user = db_session.scalar(select(User))
            if state == "inactive-user":
                user.is_active = False
            else:
                user.role = "CANDIDATE"
        db_session.commit()
        assert client.get("/api/v1/jobs").status_code == (403 if state == "wrong-role" else 401)


def test_candidate_isolation_and_internal_callback_domains(
    application: FastAPI, db_session: Session, client: TestClient
) -> None:
    interview = create_interview(db_session, invitation_token="synthetic-candidate-a-link")
    other = create_interview(db_session, invitation_token="synthetic-candidate-b-link")
    application.dependency_overrides[get_transcript_service] = lambda: build_service(db_session)
    payload = segment_payload(interview.id).model_dump(mode="json")
    callback_url = f"/api/v1/internal/interviews/{interview.id}/transcript-segments"
    # A valid recruiter cookie does not replace the internal Bearer credential.
    assert client.post(callback_url, json=payload).status_code == 401
    with TestClient(application) as candidate:
        page = candidate.get("/interviews/join/synthetic-candidate-a-link")
        assert page.status_code == 200
        assert str(interview.id) in page.text
        assert str(other.id) not in page.text
        assert 'name="csrf-token"' not in page.text
        assert 'id="interview-transcript-segments"' not in page.text
        assert candidate.get("/interviews/join/not-a-valid-capability").status_code == 404
        candidate.headers["Authorization"] = "Bearer synthetic-candidate-a-link"
        assert candidate.get(f"/api/v1/interviews/{other.id}").status_code == 401
        assert candidate.get(f"/api/v1/interviews/{interview.id}/transcript").status_code == 401
        assert (
            candidate.get(f"/interviews/{other.id}/room", follow_redirects=False).status_code == 303
        )
        rejected = candidate.post(
            "/interviews/join/synthetic-candidate-a-link/lifecycle-sync",
            json={"interview_id": str(other.id), "role": "INTERVIEWER"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["interview_id"] == str(interview.id)
        # The authenticated service can persist without a browser login or CSRF token.
        candidate.headers["Authorization"] = "Bearer synthetic-transcript-ingest-secret"
        assert candidate.post(callback_url, json=payload).status_code == 200
        assert candidate.get(f"/api/v1/interviews/{interview.id}/transcript").status_code == 401
    transcript = client.get(f"/api/v1/interviews/{interview.id}/transcript")
    assert transcript.status_code == 200
    assert len(transcript.json()["segments"]) == 1


def test_jwt_for_another_audience_cannot_authenticate(application: FastAPI) -> None:
    token = jwt.encode(
        {"sub": str(UUID(int=1)), "aud": "livekit", "exp": datetime.now(UTC) + timedelta(hours=1)},
        TEST_SECRET,
        algorithm="HS256",
    )
    with TestClient(application) as client:
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/v1/jobs").status_code == 401


def test_login_failures_are_generic_and_do_not_log_credentials(
    application: FastAPI, caplog
) -> None:
    with TestClient(application) as client:
        wrong = login(client, password="synthetic-wrong-password")
        unknown = login(client, username="missing@example.test")
        oversized = login(client, password="x" * 129)
    for response in (wrong, unknown, oversized):
        assert response.status_code == 401
        assert "The username or password is incorrect." in response.text
        assert TEST_PASSWORD not in response.text
        assert "synthetic-wrong-password" not in response.text
    assert TEST_PASSWORD not in caplog.text
    assert "synthetic-wrong-password" not in caplog.text


def test_interviewer_role_is_authorized(application: FastAPI, db_session: Session) -> None:
    user = db_session.scalar(select(User))
    user.role = "INTERVIEWER"
    db_session.commit()
    with TestClient(application) as client:
        assert login(client).status_code == 303
        assert client.get("/api/v1/jobs").status_code == 200


def test_revoked_candidate_capability_is_rejected(
    application: FastAPI, db_session: Session
) -> None:
    interview = create_interview(db_session, invitation_token="synthetic-revocable-link")
    with TestClient(application) as candidate:
        assert candidate.get("/interviews/join/synthetic-revocable-link").status_code == 200
        interview.candidate_access_token_hash = "0" * 64
        db_session.commit()
        assert candidate.get("/interviews/join/synthetic-revocable-link").status_code == 404
        assert (
            candidate.post("/interviews/join/synthetic-revocable-link/lifecycle-sync").status_code
            == 404
        )
