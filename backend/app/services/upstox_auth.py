from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
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
IST = timezone(timedelta(hours=5, minutes=30))
UPSTOX_DAILY_EXPIRY = time(hour=3, minute=30, tzinfo=IST)


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
        token_file: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.redis_url = redis_url
        self.access_token = access_token
        self.token_file = token_file

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

        now = datetime.now(timezone.utc)
        expires_at = self._next_upstox_expiry(now)
        expires_in = max(60, int((expires_at - now).total_seconds()))
        meta = {
            "storedAt": now.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "expiresAtIst": expires_at.astimezone(IST).isoformat(),
            "tokenType": payload.get("token_type", "Bearer"),
        }
        if payload.get("expires_in"):
            meta["upstoxExpiresIn"] = str(payload["expires_in"])
        await self.store_token(access_token, meta, expires_in)
        return {"configured": True, "tokenStored": True, **meta}

    async def store_token(self, token: str, meta: dict[str, Any], expires_in: int) -> None:
        UpstoxAuthService._memory_token = token
        UpstoxAuthService._memory_meta = meta
        self._store_file_token(token, meta)

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
        if UpstoxAuthService._memory_token and self._meta_is_valid(UpstoxAuthService._memory_meta):
            return UpstoxAuthService._memory_token
        file_payload = self._read_file_payload()
        if file_payload:
            meta = file_payload.get("meta") or {}
            if self._meta_is_valid(meta):
                token = file_payload.get("accessToken")
                if token and isinstance(token, str):
                    UpstoxAuthService._memory_token = token
                    UpstoxAuthService._memory_meta = meta
                    return token
            else:
                self._remove_file_token()
        return self.access_token

    async def warm_token_cache(self) -> dict[str, Any]:
        """Warm Redis and in-process memory from the most-durable token source.

        Called at application startup so the first request never cold-reads from disk.
        If only UPSTOX_ACCESS_TOKEN env var is present (no valid file token), it is
        persisted to the token file with a synthetic expiry so it survives container
        recreations even after the env var is removed.
        """
        file_payload = self._read_file_payload()
        if file_payload:
            meta = file_payload.get("meta") or {}
            token = file_payload.get("accessToken")
            if token and isinstance(token, str) and self._meta_is_valid(meta):
                expires_at_dt = self._parse_datetime(meta.get("expiresAt"))
                now = datetime.now(timezone.utc)
                expires_in = max(60, int((expires_at_dt - now).total_seconds())) if expires_at_dt else 3600
                UpstoxAuthService._memory_token = token
                UpstoxAuthService._memory_meta = meta
                if redis is not None:
                    try:
                        redis_client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                        await redis_client.set(TOKEN_KEY, token, ex=max(60, expires_in - 60))
                        await redis_client.hset(TOKEN_META_KEY, mapping={k: str(v) for k, v in meta.items()})
                        await redis_client.expire(TOKEN_META_KEY, max(60, expires_in - 60))
                        await redis_client.aclose()
                    except Exception:
                        pass
                return {"source": "file", "warmed": True, "expiresAt": meta.get("expiresAt")}

        if self.access_token:
            now = datetime.now(timezone.utc)
            expires_at_dt = self._next_upstox_expiry(now)
            expires_in = max(60, int((expires_at_dt - now).total_seconds()))
            meta = {
                "storedAt": now.isoformat(),
                "expiresAt": expires_at_dt.isoformat(),
                "expiresAtIst": expires_at_dt.astimezone(IST).isoformat(),
                "tokenType": "Bearer",
            }
            await self.store_token(self.access_token, meta, expires_in)
            return {"source": "environment", "warmed": True, "persisted": True, "expiresAt": expires_at_dt.isoformat()}

        return {"source": None, "warmed": False}

    async def token_status(self) -> dict[str, Any]:
        token = await self.get_token()
        source = self._token_source(token)
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
        if source == "file":
            file_payload = self._read_file_payload()
            if file_payload:
                meta = dict(file_payload.get("meta") or {})

        return {
            "configured": self.configured,
            "hasToken": bool(token),
            "source": source,
            "expiresAt": meta.get("expiresAt"),
            "expiresAtIst": meta.get("expiresAtIst"),
            "tokenType": meta.get("tokenType"),
        }

    def _token_source(self, token: str | None) -> str | None:
        if not token:
            return None
        if self.access_token and token == self.access_token:
            return "environment"
        file_payload = self._read_file_payload()
        if file_payload and token == file_payload.get("accessToken"):
            return "file"
        if UpstoxAuthService._memory_token and token == UpstoxAuthService._memory_token:
            return "memory"
        return "redis"

    def _store_file_token(self, token: str, meta: dict[str, Any]) -> None:
        if not self.token_file:
            return
        try:
            token_path = Path(self.token_file)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"accessToken": token, "meta": meta}
            tmp_path = token_path.with_suffix(f"{token_path.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(tmp_path, 0o600)
            tmp_path.replace(token_path)
            os.chmod(token_path, 0o600)
        except Exception:
            return

    def _get_file_token(self) -> str | None:
        payload = self._read_file_payload()
        if not payload:
            return None
        meta = payload.get("meta") or {}
        if not self._meta_is_valid(meta):
            self._remove_file_token()
            return None
        token = payload.get("accessToken")
        return token if isinstance(token, str) and token else None

    def _read_file_payload(self) -> dict[str, Any] | None:
        if not self.token_file:
            return None
        try:
            token_path = Path(self.token_file)
            if not token_path.exists():
                return None
            payload = json.loads(token_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _remove_file_token(self) -> None:
        if not self.token_file:
            return
        try:
            Path(self.token_file).unlink(missing_ok=True)
        except Exception:
            return

    def _meta_is_valid(self, meta: dict[str, Any]) -> bool:
        expires_at = self._parse_datetime(meta.get("expiresAt"))
        if not expires_at:
            return True
        return expires_at > datetime.now(timezone.utc)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _next_upstox_expiry(now: datetime) -> datetime:
        now_ist = now.astimezone(IST)
        expiry_ist = datetime.combine(now_ist.date(), UPSTOX_DAILY_EXPIRY)
        if now_ist >= expiry_ist:
            expiry_ist += timedelta(days=1)
        return expiry_ist.astimezone(timezone.utc)
