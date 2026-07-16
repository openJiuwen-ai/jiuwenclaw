"""Errors raised by the Celia Memory adapter."""


class CeliaError(RuntimeError):
    """Base exception for adapter failures."""


class CeliaConfigError(CeliaError):
    """Invalid or conflicting Celia configuration."""


class CeliaUnavailable(CeliaError):
    """Celia binary or process is unavailable."""


class CeliaMcpError(CeliaError):
    """MCP protocol or tool error."""


class CeliaMcpTimeout(CeliaMcpError, TimeoutError):
    """MCP request timeout."""
