"""进程入口：等价于 `uvicorn jiuwenclaw_manager.app:app`。"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from jiuwenclaw_manager.infrastructure.config import settings

    uvicorn.run(
        "jiuwenclaw_manager.app:app",
        host=settings.host,
        port=settings.port,
        factory=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
