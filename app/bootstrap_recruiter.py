import logging

from app.config import get_settings
from app.database import SessionFactory
from app.errors import RecrUnionError
from app.repositories.auth import AuthRepository
from app.services.auth import AuthService

logger = logging.getLogger(__name__)


def main() -> int:
    """Create the initial recruiter once, after Alembic upgrade, from local environment."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    try:
        with SessionFactory() as session:
            service = AuthService(
                AuthRepository(session),
                secret=settings.jwt_secret,
                token_minutes=settings.access_token_minutes,
            )
            created = service.bootstrap_recruiter(
                settings.recruiter_username, settings.recruiter_password.get_secret_value()
            )
    except RecrUnionError as error:
        logger.error("Recruiter bootstrap failed code=%s message=%s", error.code, error)
        return 1
    logger.info("Recruiter bootstrap completed created=%s", created)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
