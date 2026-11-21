from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger


def get_logger(name: str) -> "Logger":
    return logger.bind(module=name)
