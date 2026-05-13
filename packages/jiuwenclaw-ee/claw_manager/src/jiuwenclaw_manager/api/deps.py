from collections.abc import AsyncGenerator

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from jiuwenclaw_manager.infrastructure.db import get_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_http_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise RuntimeError("http_client not initialized on app.state")
    return client
