from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

try:
    import asyncpg  # type: ignore
except Exception:  # pragma: no cover
    asyncpg = None


class EventJournal:
    """Institutional event journal with PostgreSQL persistence and memory fallback."""

    _events: deque[dict[str, Any]] = deque(maxlen=5_000)
    _seen_keys: set[str] = set()

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url
        self._pool: Any | None = None

    async def connect(self) -> None:
        if asyncpg is None or not self.database_url:
            return
        try:
            self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=3)
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    create table if not exists execution_events (
                        id bigserial primary key,
                        event_id text unique not null,
                        created_at timestamptz default now(),
                        event_type text not null,
                        severity text not null,
                        symbol text,
                        message text not null,
                        payload jsonb not null
                    )
                    """
                )
        except Exception:
            self._pool = None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def record(
        self,
        event_type: str,
        message: str,
        *,
        symbol: str | None = None,
        severity: str = "INFO",
        payload: dict[str, Any] | None = None,
        event_key: str | None = None,
    ) -> dict[str, Any] | None:
        key = event_key or f"{event_type}:{symbol}:{message}:{datetime.now(timezone.utc).isoformat()}"
        if key in EventJournal._seen_keys:
            return None
        EventJournal._seen_keys.add(key)
        if len(EventJournal._seen_keys) > 20_000:
            EventJournal._seen_keys = set(list(EventJournal._seen_keys)[-10_000:])
        event = {
            "eventId": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "severity": severity,
            "symbol": symbol,
            "message": message,
            "payload": payload or {},
        }
        EventJournal._events.append(event)
        if self._pool is not None:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        insert into execution_events(event_id, event_type, severity, symbol, message, payload)
                        values($1, $2, $3, $4, $5, $6::jsonb)
                        on conflict(event_id) do nothing
                        """,
                        event["eventId"],
                        event_type,
                        severity,
                        symbol,
                        message,
                        json.dumps(event["payload"]),
                    )
            except Exception:
                pass
        return event

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._pool is not None:
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        select event_id, created_at, event_type, severity, symbol, message, payload
                        from execution_events
                        order by created_at desc
                        limit $1
                        """,
                        limit,
                    )
                    return [
                        {
                            "eventId": row["event_id"],
                            "timestamp": row["created_at"].isoformat(),
                            "type": row["event_type"],
                            "severity": row["severity"],
                            "symbol": row["symbol"],
                            "message": row["message"],
                            "payload": dict(row["payload"] or {}),
                        }
                        for row in rows
                    ]
            except Exception:
                pass
        return list(EventJournal._events)[-limit:][::-1]
