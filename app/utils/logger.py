import sys
from loguru import logger
from app.config import settings

logger.remove()
logger.add(
    sys.stdout,
    level=settings.log_level,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
           "<level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
           "<level>{message}</level>"
)
logger.add(
    "logs/agent_{time:YYYY-MM-DD}.log",
    level=settings.log_level,
    rotation="00:00",
    retention="14 days",
    encoding="utf-8"
)

__all__ = ["logger"]
