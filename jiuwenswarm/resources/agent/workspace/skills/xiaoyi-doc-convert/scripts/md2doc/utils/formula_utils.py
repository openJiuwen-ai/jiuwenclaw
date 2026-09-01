"""Formula utilities for md2doc skill."""
import re


# Default formula pattern - same as in main project
FORMULA_PATTERN = r'(\$[^\$]{1,200}\$)'


def has_formula(content: str, formula_pattern: str = FORMULA_PATTERN) -> bool:
    """Check if content contains formula patterns."""
    match = re.search(formula_pattern, content)
    return match is not None


def protect_formulas(text: str, content_filters: list, formula_pattern: str = FORMULA_PATTERN) -> str:
    """Protect formulas by applying filters only to non-formula parts.

    This simplified version returns the text unchanged as the skill
    doesn't use complex filter chains like the main project.
    """
    return text

