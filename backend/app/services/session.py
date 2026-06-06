from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class MarketPhase(StrEnum):
    PRE_MARKET_ANALYSIS = "PRE_MARKET_ANALYSIS"
    LIVE_MARKET = "LIVE_MARKET"
    POST_MARKET_ANALYSIS = "POST_MARKET_ANALYSIS"
    CLOSED_MARKET = "CLOSED_MARKET"


@dataclass(frozen=True)
class SessionState:
    phase: MarketPhase
    is_trading_session: bool
    execution_allowed: bool
    label: str
    reason: str
    timestamp_ist: str


def current_session_state(now: datetime | None = None) -> SessionState:
    current = (now or datetime.now(IST)).astimezone(IST)
    weekday = current.weekday()
    current_time = current.time()

    if weekday >= 5:
        return SessionState(
            phase=MarketPhase.CLOSED_MARKET,
            is_trading_session=False,
            execution_allowed=False,
            label="Weekend closed-market analysis",
            reason="Indian exchanges are closed on Saturday and Sunday.",
            timestamp_ist=current.isoformat(),
        )

    if time(8, 30) <= current_time < time(9, 15):
        return SessionState(
            phase=MarketPhase.PRE_MARKET_ANALYSIS,
            is_trading_session=True,
            execution_allowed=False,
            label="Pre-market preparation",
            reason="Build levels, option-chain bias and risk plan before 09:15 IST; do not place live F&O scalps yet.",
            timestamp_ist=current.isoformat(),
        )

    if time(9, 15) <= current_time <= time(15, 30):
        return SessionState(
            phase=MarketPhase.LIVE_MARKET,
            is_trading_session=True,
            execution_allowed=True,
            label="Live market scalping window",
            reason="NSE/BSE regular session is open; execution can be considered if all risk gates pass.",
            timestamp_ist=current.isoformat(),
        )

    if time(15, 30) < current_time <= time(16, 15):
        return SessionState(
            phase=MarketPhase.POST_MARKET_ANALYSIS,
            is_trading_session=True,
            execution_allowed=False,
            label="Post-market review",
            reason="Market is closed for fresh scalps; analyze closes, OI shifts, fills and next-session levels.",
            timestamp_ist=current.isoformat(),
        )

    return SessionState(
        phase=MarketPhase.CLOSED_MARKET,
        is_trading_session=False,
        execution_allowed=False,
        label="Closed-market analysis",
        reason="Outside Indian regular market hours; use only historical/last available Upstox data.",
        timestamp_ist=current.isoformat(),
    )
