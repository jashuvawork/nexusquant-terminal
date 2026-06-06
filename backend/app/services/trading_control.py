from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

STOP_KEY = "nexusquant:trading:stopped"
STOP_META_KEY = "nexusquant:trading:stop_meta"
CAPITAL_KEY = "nexusquant:trading:capital"
CAPITAL_META_KEY = "nexusquant:trading:capital_meta"


class TradingControl:
    """Runtime kill switch for automated trading."""

    _memory_stopped = False
    _memory_meta: dict[str, Any] = {}

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url

    async def stop(self, reason: str = "Manual emergency stop") -> dict[str, Any]:
        meta = {
            "stopped": "true",
            "reason": reason,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        TradingControl._memory_stopped = True
        TradingControl._memory_meta = meta
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                await client.set(STOP_KEY, "true")
                await client.hset(STOP_META_KEY, mapping=meta)
                await client.aclose()
            except Exception:
                pass
        return await self.status()

    async def resume(self, reason: str = "Manual resume") -> dict[str, Any]:
        meta = {
            "stopped": "false",
            "reason": reason,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        TradingControl._memory_stopped = False
        TradingControl._memory_meta = meta
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                await client.set(STOP_KEY, "false")
                await client.hset(STOP_META_KEY, mapping=meta)
                await client.aclose()
            except Exception:
                pass
        return await self.status()


    async def set_capital(self, amount: float, reason: str = "Capital updated") -> dict[str, Any]:
        amount = max(0.0, float(amount))
        meta = {
            "amount": str(amount),
            "reason": reason,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        TradingControl._memory_meta = {**TradingControl._memory_meta, "capital": amount, "capitalUpdatedAt": meta["updatedAt"]}
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                await client.set(CAPITAL_KEY, str(amount))
                await client.hset(CAPITAL_META_KEY, mapping=meta)
                await client.aclose()
            except Exception:
                pass
        return await self.capital_status()

    async def get_capital(self) -> float:
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                value = await client.get(CAPITAL_KEY)
                await client.aclose()
                if value is not None:
                    return max(0.0, float(value))
            except Exception:
                pass
        return float(TradingControl._memory_meta.get("capital") or 0.0)

    async def capital_status(self) -> dict[str, Any]:
        amount = await self.get_capital()
        meta: dict[str, Any] = {}
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                meta = await client.hgetall(CAPITAL_META_KEY)
                await client.aclose()
            except Exception:
                pass
        return {
            "tradingCapital": amount,
            "reason": meta.get("reason") or "Runtime capital",
            "updatedAt": meta.get("updatedAt") or TradingControl._memory_meta.get("capitalUpdatedAt"),
        }

    async def is_stopped(self) -> bool:
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                value = await client.get(STOP_KEY)
                await client.aclose()
                if value is not None:
                    return value.lower() == "true"
            except Exception:
                pass
        return TradingControl._memory_stopped

    async def status(self) -> dict[str, Any]:
        stopped = await self.is_stopped()
        meta = dict(TradingControl._memory_meta)
        if redis is not None:
            try:
                client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                redis_meta = await client.hgetall(STOP_META_KEY)
                await client.aclose()
                if redis_meta:
                    meta = redis_meta
            except Exception:
                pass
        return {
            "autoTradingStopped": stopped,
            "reason": meta.get("reason"),
            "updatedAt": meta.get("updatedAt"),
        }
