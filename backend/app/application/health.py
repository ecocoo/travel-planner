from sqlalchemy.exc import SQLAlchemyError

from backend.app.application.exceptions import DatabaseUnavailableError
from backend.app.infrastructure.db.database import get_engine
from backend.app.infrastructure.repositories.catalog import check_ready
from backend.app.shared.logging_config import get_logger

logger = get_logger(__name__)


async def check_readiness() -> None:
    try:
        async with get_engine().connect() as connection:
            await check_ready(connection)
    except SQLAlchemyError as error:
        code = getattr(getattr(error, "orig", None), "sqlstate", None)
        logger.bind(error_type=type(error).__name__, sqlstate=code).error(
            "Database readiness check failed"
        )
        raise DatabaseUnavailableError(
            "Database is unavailable or migrations are pending"
        ) from error
