"""Filter base class for md2doc skill."""
from abc import ABC, abstractmethod

from logger import get_logger

LOG = get_logger("Filter")


class Filter(ABC):
    """Content filter interface."""

    def __init__(self, name: str = '', description: str = ''):
        super().__init__()
        LOG.debug(f"create filter {name}, {description}")
        self.name = name
        self.description = description

    @abstractmethod
    def apply(self, content: str, request_id: str = "") -> str:
        """Apply filter to content.

        Args:
            content: Input text content
            request_id: Request ID

        Returns:
            Filtered text content
        """
        pass
