# Authentication and Access Boundaries

## M11.5 scope

Reviewed WBS item 6.1.1 is implemented before F5/F6 because recruiter endpoints previously lacked
enforcement. This is a minimal application authentication foundation, not completion of all M16
security hardening. It preserves the approved username/password plus JWT design.

## Principals and credentials

| Principal | Credential | Access |
|---|---|---|
| Recruiter/interviewer | Signed JWT in an HttpOnly cookie plus an active database session | Recruiter pages and APIs |
| Candidate | Existing opaque interview invitation capability | Assigned interview join and candidate lifecycle only |
| Transcription agent | Existing internal Bearer secret | Internal final-transcript ingest callback only |
| Media participant | Existing short-lived LiveKit token | Exact provider room and server-selected participant |

These credentials are not interchangeable. The browser cannot select an authoritative role.
Candidate invitation links continue to work without a login, password, or registration.

## Recruiter authentication

The initial account is provisioned explicitly from environment configuration after migrations.
There is no signup or user-administration dashboard. Passwords use PBKDF2-HMAC-SHA256 with 600,000
iterations and a fresh random salt. Authentication failures use a generic message.

Recruiter JWTs use a fixed HS256 algorithm, issuer, audience, user ID, random session ID, expiry,
and a random session-bound CSRF token. Each request also checks the database session and the
current user's active status/role. Logout deletes that session, preventing replay of its cookie.
Expired sessions are removed when a subsequent login creates a new one. Session lifetimes are
bounded and configurable; no refresh-token infrastructure or browser storage token is used.

Cookies are host-only, HttpOnly, SameSite=Lax, and Secure when the request or configured public
application origin is HTTPS. Local HTTP development is supported. Private responses are not cached.
Recruiter pages use `Referrer-Policy: same-origin`, preserving native form Origin checks while
preventing external referrer disclosure. Candidate invitation pages retain `no-referrer` to hide
capability URLs even from same-origin requests. Opaque `Origin: null` remains rejected for login.

## Authorization and CSRF

Shared FastAPI dependencies protect the recruiter route families: home, jobs and JD workflows,
publishing, applications/CV processing, company documents, email intake, F1 screening/ranking,
F2 scoring, assistant workflows, interview management/interviewer room, transcript retrieval,
task status, F11 probes/co-pilot, and API documentation. Future F5/F6 routes must use the same dependency.

Anonymous recruiter APIs return 401; recruiter pages redirect to a local login destination.
An authenticated account with an unauthorized role receives 403. Browser destinations cannot be
external URLs. Candidates cannot substitute an interview ID to obtain a recruiter room or data.

Recruiter POST/PUT/PATCH/DELETE requests require a CSRF token from their authenticated page, passed
in `X-CSRF-Token` or a native form field. Origin/fetch-metadata checks supplement this validation.
The shared frontend helper adds the header only to same-origin mutations and reports session
expiry without disconnecting media. Login uses an independent double-submit form token. Logout
is a protected POST. Candidate capability endpoints and service callbacks retain their own
authorization and do not use recruiter CSRF credentials.

## Limits and deferred work

All authorized recruiters share the single application's recruiter workspace; tenant isolation,
SSO, MFA, account management/recovery, and refresh tokens are outside this milestone. Login rate
limiting/abuse controls and broader deployment/security hardening remain M16 work. Existing
candidate-link expiry/revocation semantics and LiveKit media-token behavior are preserved.
No recordings, transcript schema changes, F5/F6 implementation, or React migration are introduced.
