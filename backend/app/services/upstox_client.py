from __future__ import annotations

from time import perf_counter
from typing import Any
from urllib.parse import quote

import httpx

from app.services.upstox_auth import UpstoxAuthService

UPSTOX_API_BASE = "https://api.upstox.com"
UPSTOX_HFT_BASE = "https://api-hft.upstox.com"


class UpstoxDataError(RuntimeError):
    pass


class UpstoxAuthRequired(UpstoxDataError):
    pass


class UpstoxClient:
    """Real Upstox API adapter.

    This class never fabricates market, portfolio or order data. If Upstox is not
    authenticated or an endpoint fails, callers receive an explicit exception and
    can display a disconnected/configuration state to the user.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        auth_service: UpstoxAuthService | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.auth_service = auth_service

    async def _access_token(self) -> str:
        token = await self.auth_service.get_token() if self.auth_service else None
        if not token:
            raise UpstoxAuthRequired("Upstox access token is missing. Open /api/upstox/login-url and complete broker login.")
        return token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        started = perf_counter()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, url, params=params, json=json, headers=headers)
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        if response.status_code >= 400:
            raise UpstoxDataError(f"Upstox API error {response.status_code}: {response.text}")
        payload = response.json()
        if isinstance(payload, dict):
            payload["nexusquant_latency_ms"] = elapsed_ms
            payload["nexusquant_endpoint"] = url
        if payload.get("status") and payload.get("status") != "success":
            raise UpstoxDataError(f"Upstox API returned non-success status: {payload}")
        return payload

    async def health(self) -> dict[str, Any]:
        configured = bool(self.api_key and self.api_secret)
        token_status = await self.auth_service.token_status() if self.auth_service else {"hasToken": False}
        has_token = bool(token_status.get("hasToken"))
        return {
            "configured": configured,
            "hasAccessToken": has_token,
            "tokenExpiresAt": token_status.get("expiresAt"),
            "streamer": "MarketDataStreamerV3-ready",
            "brokerHealth": 100 if configured and has_token else 0,
            "mode": "live-ready" if configured and has_token else "auth-required",
        }


    async def news_headlines(self, instrument_key: str, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v2/news/headlines",
            params={"instrument_key": instrument_key, "page": page, "page_size": page_size},
        )

    async def ltp(self, instrument_keys: list[str]) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v3/market-quote/ltp",
            params={"instrument_key": ",".join(instrument_keys)},
        )

    async def full_market_quote(self, instrument_keys: list[str]) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v2/market-quote/quotes",
            params={"instrument_key": ",".join(instrument_keys)},
        )


    async def option_contracts(self, instrument_key: str, expiry_date: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"instrument_key": instrument_key}
        if expiry_date:
            params["expiry_date"] = expiry_date
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v2/option/contract",
            params=params,
        )

    async def option_chain(self, instrument_key: str, expiry_date: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v2/option/chain",
            params={"instrument_key": instrument_key, "expiry_date": expiry_date},
        )


    async def historical_candles(
        self,
        instrument_key: str,
        unit: str = "minutes",
        interval: int = 1,
        to_date: str | None = None,
        from_date: str | None = None,
    ) -> dict[str, Any]:
        encoded = quote(instrument_key, safe="")
        if to_date and from_date:
            url = f"{UPSTOX_API_BASE}/v3/historical-candle/{encoded}/{unit}/{interval}/{to_date}/{from_date}"
        elif to_date:
            url = f"{UPSTOX_API_BASE}/v3/historical-candle/{encoded}/{unit}/{interval}/{to_date}"
        else:
            url = f"{UPSTOX_API_BASE}/v3/historical-candle/intraday/{encoded}/{unit}/{interval}"
        return await self._request("GET", url)

    async def intraday_candles(self, instrument_key: str, unit: str = "minutes", interval: int = 1) -> dict[str, Any]:
        encoded = quote(instrument_key, safe="")
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v3/historical-candle/intraday/{encoded}/{unit}/{interval}",
        )

    async def funds_v3(self) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{UPSTOX_API_BASE}/v3/user/get-funds-and-margin",
            extra_headers={"Api-Version": "3.0"},
        )

    async def funds_v2(self) -> dict[str, Any]:
        return await self._request("GET", f"{UPSTOX_API_BASE}/v2/user/get-funds-and-margin")

    async def funds(self) -> dict[str, Any]:
        try:
            payload = await self.funds_v3()
            payload["nexusquant_source"] = "upstox_v3"
            return payload
        except UpstoxDataError as first_error:
            try:
                payload = await self.funds_v2()
                payload["nexusquant_source"] = "upstox_v2"
                payload["nexusquant_v3_error"] = str(first_error)
                return payload
            except UpstoxDataError as second_error:
                raise UpstoxDataError(f"Funds V3 failed: {first_error}; Funds V2 failed: {second_error}") from second_error

    async def positions(self) -> dict[str, Any]:
        return await self._request("GET", f"{UPSTOX_API_BASE}/v2/portfolio/short-term-positions")

    async def orders(self) -> dict[str, Any]:
        return await self._request("GET", f"{UPSTOX_API_BASE}/v2/order/retrieve-all")

    async def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"{UPSTOX_HFT_BASE}/v2/order/place", json=order)
