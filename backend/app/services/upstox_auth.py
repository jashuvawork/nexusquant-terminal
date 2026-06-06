from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

try:
    import redis.asyncio as redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None


UPSTOX_AUTHORIZE_URL = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
TOKEN_KEY = "nexusquant:upstox:access_token"
TOKEN_META_KEY = "nexusquant:upstox:token_meta"


class UpstoxAuthError(RuntimeError):
    pass


class UpstoxAuthService:
    """OAuth helper for Upstox authorization-code login and token caching."""

    _memory_token: str | None = None
    _memory_meta: dict[str, Any] = {}

    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        redirect_uri: str | None,
        redis_url: str,
        access_token: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.redis_url = redis_url
        self.access_token = access_token

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.redirect_uri)

    def login_url(self) -> str:
        if not self.api_key or not self.redirect_uri:
            raise UpstoxAuthError("UPSTOX_API_KEY and UPSTOX_REDIRECT_URI must be configured.")
        return f"{UPSTOX_AUTHORIZE_URL}?{urlencode({'response_type': 'code', 'client_id': self.api_key, 'redirect_uri': self.redirect_uri})}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        if not self.configured:
            raise UpstoxAuthError("UPSTOX_API_KEY, UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI must be configured.")

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                UPSTOX_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code >= 400:
            raise UpstoxAuthError(f"Upstox token exchange failed: {response.text}")

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise UpstoxAuthError("Upstox did not return an access_token.")

        expires_in = int(payload.get("expires_in") or 24 * 60 * 60)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        meta = {
            "storedAt": datetime.now(timezone.utc).isoformat(),
            "expiresAt": expires_at.isoformat(),
            "tokenType": payload.get("token_type", "Bearer"),
        }
        await self.store_token(access_token, meta, expires_in)
        return {"configured": True, "tokenStored": True, **meta}

    async def store_token(self, token: str, meta: dict[str, Any], expires_in: int) -> None:
        UpstoxAuthService._memory_token = token
        UpstoxAuthService._memory_meta = meta

        if redis is None:
            return
        try:
            redis_client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
            await redis_client.set(TOKEN_KEY, token, ex=max(60, expires_in - 60))
            await redis_client.hset(TOKEN_META_KEY, mapping={key: str(value) for key, value in meta.items()})
            await redis_client.expire(TOKEN_META_KEY, max(60, expires_in - 60))
            await redis_client.aclose()
        except Exception:
            return

    async def get_token(self) -> str | None:
        if redis is not None:
            try:
                redis_client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                token = await redis_client.get(TOKEN_KEY)
                await redis_client.aclose()
                if token:
                    return token
            except Exception:
                pass
        return UpstoxAuthService._memory_token or self.access_token

    async def token_status(self) -> dict[str, Any]:
        token = await self.get_token()
        meta = dict(UpstoxAuthService._memory_meta)
        if redis is not None:
            try:
                redis_client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                redis_meta = await redis_client.hgetall(TOKEN_META_KEY)
                await redis_client.aclose()
                if redis_meta:
                    meta = redis_meta
            except Exception:
                pass

        return {
            "configured": self.configured,
            "hasToken": bool(token),
            "source": "environment" if self.access_token and token == self.access_token else "redis" if token else None,
            "expiresAt": meta.get("expiresAt"),
            "tokenType": meta.get("tokenType"),
        }
