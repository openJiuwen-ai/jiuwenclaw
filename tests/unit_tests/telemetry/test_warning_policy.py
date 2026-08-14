import pytest


def test_project_invalid_escape_remains_an_error() -> None:
    source = r'''PROJECT_PATTERN = "\."'''

    with pytest.raises(SyntaxError, match="invalid escape sequence"):
        compile(source, "jiuwenswarm/warning_probe.py", "exec")
