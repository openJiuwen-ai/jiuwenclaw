"""Filter manager for md2doc skill."""
import re

from logger import get_logger
from filters.filter import Filter

LOG = get_logger("FilterManager")


class FilterManager:
    """Manages content filters."""

    def __init__(self):
        self.filters = {}
        self.filter_chains = {}

    def register_filter(self, filter_instance: Filter):
        """Register a filter."""
        self.filters[filter_instance.name] = filter_instance
        LOG.debug(f"Registered filter: {filter_instance.name}")

    def get_filter(self, name: str) -> Filter:
        """Get a filter by name."""
        return self.filters.get(name)

    def apply_filters(self, content: str, filter_names: list, request_id: str = "") -> str:
        """Apply a chain of filters to content."""
        for name in filter_names:
            filter_instance = self.get_filter(name)
            if filter_instance:
                LOG.debug(f"{request_id}, apply content filter: {filter_instance.description}")
                content = filter_instance.apply(content, request_id)
            else:
                LOG.warning(f"{request_id}, filter not found: {name}")
        return content


class RegexFilter(Filter):
    """Regex-based filter."""

    def __init__(self, name: str, pattern: str, replacement: str, description: str = ""):
        super().__init__(name=name, description=description)
        self.pattern = re.compile(pattern, re.DOTALL | re.MULTILINE)
        self.replacement = replacement

    def apply(self, content: str, request_id: str = "") -> str:
        return self.pattern.sub(self.replacement, content)


def create_filter_manager(config: dict) -> FilterManager:
    """Create and configure filter manager from config."""
    manager = FilterManager()

    # Register regex filters from config
    regex_filters = config.get('regex_filters', {})
    for name, filter_config in regex_filters.items():
        pattern = filter_config.get('pattern', '')
        replacement = filter_config.get('replacement', '')
        description = filter_config.get('description', '')
        manager.register_filter(RegexFilter(name, pattern, replacement, description))

    return manager
