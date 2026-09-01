"""Converter base class for md2doc skill."""
from abc import ABC, abstractmethod

from logger import get_logger

LOG = get_logger("Converter")


class Converter(ABC):
    """Abstract base class for converters."""

    def __init__(self, name: str = ''):
        super().__init__()
        LOG.debug(f"create converter {name}")
        self.name = name

    @abstractmethod
    def convert(self, content: str, target_path: str, **kwargs):
        """Convert content and save to target path.

        Args:
            content: Content to convert.
            target_path: Target file path.
            **kwargs: Optional parameters.
        """
        pass
