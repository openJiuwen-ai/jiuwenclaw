# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional PostgreSQL-backed JSON document store.

The project keeps JSON/YAML stores as the standalone default. Enterprise mode
can opt into this adapter with `JIUWENAVATAR_STORE_BACKEND=postgres` and
`DATABASE_URL`/`POSTGRES_DSN`. The adapter imports PostgreSQL drivers lazily so
developer installs without those dependencies keep working.
"""

from __future__ import annotations

import json
import os
from typing import Any


class PostgresJsonStoreUnavailable(RuntimeError):
    pass


def postgres_store_enabled() -> bool:
    backend = os.getenv("JIUWENAVATAR_STORE_BACKEND", "").strip().lower()
    return backend in {"postgres", "postgresql", "pg"}


class PostgresJsonStore:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or ""
        if not self.dsn:
            raise PostgresJsonStoreUnavailable("DATABASE_URL/POSTGRES_DSN is not configured")
        self._driver = self._load_driver()
        self._ensure_table()

    @staticmethod
    def _load_driver() -> Any:
        try:
            import psycopg  # type: ignore

            return ("psycopg", psycopg)
        except Exception:
            try:
                import psycopg2  # type: ignore

                return ("psycopg2", psycopg2)
            except Exception as exc:  # noqa: BLE001
                raise PostgresJsonStoreUnavailable("psycopg/psycopg2 is not installed") from exc

    def _connect(self) -> Any:
        _name, driver = self._driver
        return driver.connect(self.dsn)

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jiuwenavatar_json_store (
                        namespace TEXT NOT NULL,
                        key TEXT NOT NULL,
                        group_id TEXT NOT NULL DEFAULT '',
                        owner_user_id TEXT NOT NULL DEFAULT '',
                        data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (namespace, key)
                    )
                    """
                )
            conn.commit()

    def list(self, *, group_id: str | None = None, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        clauses = ["namespace = %s"]
        args: list[Any] = [self.namespace]
        if group_id:
            clauses.append("group_id = %s")
            args.append(group_id)
        if owner_user_id:
            clauses.append("owner_user_id = %s")
            args.append(owner_user_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT data FROM jiuwenavatar_json_store WHERE {' AND '.join(clauses)}",
                    args,
                )
                rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = row[0]
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                out.append(data)
        return out

    def get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT data FROM jiuwenavatar_json_store WHERE namespace = %s AND key = %s",
                    (self.namespace, key),
                )
                row = cur.fetchone()
        if not row:
            return None
        data = row[0]
        if isinstance(data, str):
            data = json.loads(data)
        return data if isinstance(data, dict) else None

    def save(
        self,
        key: str,
        data: dict[str, Any],
        *,
        group_id: str = "",
        owner_user_id: str = "",
    ) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jiuwenavatar_json_store(namespace, key, group_id, owner_user_id, data)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        group_id = EXCLUDED.group_id,
                        owner_user_id = EXCLUDED.owner_user_id,
                        data = EXCLUDED.data,
                        updated_at = now()
                    """,
                    (self.namespace, key, group_id, owner_user_id, payload),
                )
            conn.commit()

    def delete(self, key: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM jiuwenavatar_json_store WHERE namespace = %s AND key = %s",
                    (self.namespace, key),
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted
