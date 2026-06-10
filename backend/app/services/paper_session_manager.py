from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class PaperSessionManager:
    """Rotating paper-trading sessions within a calendar day.

    A session ends when consecutive-loss or profit-target rules fire. The
    completed session is persisted and a fresh session starts immediately.
    """

    _current: dict[str, Any] | None = None
    _completed: deque[dict[str, Any]] = deque(maxlen=2_000)
    _day_totals: dict[str, float] = {"grossProfit": 0.0, "grossLoss": 0.0, "netPnl": 0.0}
    _file_loaded: bool = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._load_file()
        if self._current is None:
            self.start_session("initial")

    def current_id(self) -> str:
        return str((self._current or {}).get("id") or "")

    def current(self) -> dict[str, Any]:
        return dict(self._current or {})

    def restore_current_session(self, session_id: str | None, *, started_at: str | None = None, reason: str = "restored_paper_trades") -> dict[str, Any]:
        if not session_id:
            return self.current()
        if str((self._current or {}).get("id") or "") == session_id:
            return self.current()
        parts = session_id.split("-")
        trading_day = _utc_day()
        session_number = len(self.completed_today()) + 1
        if len(parts) >= 7 and parts[0] == "paper" and parts[1] == "session":
            trading_day = "-".join(parts[2:5])
            try:
                session_number = int(parts[5])
            except ValueError:
                session_number = len(self.completed_today()) + 1
        self._current = {
            "id": session_id,
            "tradingDay": trading_day,
            "sessionNumber": session_number,
            "startedAt": started_at or _utc_now_iso(),
            "startReason": reason,
            "endedAt": None,
            "endReason": None,
            "status": "ACTIVE",
        }
        return dict(self._current)

    def _dedupe_sessions(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for session in sessions:
            session_id = str(session.get("id") or session.get("sessionId") or "")
            if session_id and session_id in seen:
                continue
            if session_id:
                seen.add(session_id)
            unique.append(session)
        return unique

    def completed_today(self) -> list[dict[str, Any]]:
        day = _utc_day()
        today = [session for session in list(self._completed) if str(session.get("tradingDay") or "") == day]
        return self._dedupe_sessions(today)

    def day_aggregate(self) -> dict[str, Any]:
        sessions = self.completed_today()
        current_report = self.build_report([])
        gross_profit = sum(float(session.get("grossProfit") or 0) for session in sessions) + float(current_report.get("grossProfit") or 0)
        gross_loss = sum(float(session.get("grossLoss") or 0) for session in sessions) + float(current_report.get("grossLoss") or 0)
        net = gross_profit - gross_loss
        trades = sum(int(session.get("paperTrades") or 0) for session in sessions) + int(current_report.get("paperTrades") or 0)
        wins = sum(int(session.get("wins") or 0) for session in sessions) + int(current_report.get("wins") or 0)
        return {
            "tradingDay": _utc_day(),
            "sessionsCompleted": len(sessions),
            "sessionsIncludingCurrent": len(sessions) + 1,
            "paperTrades": trades,
            "wins": wins,
            "losses": trades - wins,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "netPnl": round(net, 2),
            "profitFactor": round(gross_profit / gross_loss, 3) if gross_loss else round(gross_profit, 3),
        }

    def start_session(self, reason: str = "manual") -> dict[str, Any]:
        day = _utc_day()
        session_number = len(self.completed_today()) + 1
        self._current = {
            "id": f"paper-session-{day}-{session_number}-{uuid4().hex[:8]}",
            "tradingDay": day,
            "sessionNumber": session_number,
            "startedAt": _utc_now_iso(),
            "startReason": reason,
            "endedAt": None,
            "endReason": None,
            "status": "ACTIVE",
        }
        return dict(self._current)

    def close_session(self, report: dict[str, Any], end_reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._current:
            return {}
        closed = {
            **self._current,
            **report,
            "endedAt": _utc_now_iso(),
            "endReason": end_reason,
            "status": "COMPLETED",
            **(extra or {}),
        }
        existing_ids = {str(session.get("id") or "") for session in self._completed}
        if str(closed.get("id") or "") not in existing_ids:
            self._completed.append(closed)
        self._append_file(closed)
        self._current = None
        return closed

    def build_report(self, closed_trades: list[Any]) -> dict[str, Any]:
        wins = [trade for trade in closed_trades if float(getattr(trade, "pnl", 0) or 0) > 0]
        losses = [trade for trade in closed_trades if float(getattr(trade, "pnl", 0) or 0) < 0]
        gross_profit = sum(float(trade.pnl) for trade in wins)
        gross_loss = abs(sum(float(trade.pnl) for trade in losses))
        net = gross_profit - gross_loss
        capital = float(self.settings.trading_capital_default or 0)
        profit_pct = net / capital * 100 if capital > 0 and net > 0 else 0.0
        loss_pct = abs(net) / capital * 100 if capital > 0 and net < 0 else 0.0
        consecutive_losses = 0
        for trade in reversed(closed_trades):
            if float(trade.pnl) < 0:
                consecutive_losses += 1
            elif float(trade.pnl) > 0:
                break
        return {
            "sessionId": self.current_id(),
            "sessionNumber": int((self._current or {}).get("sessionNumber") or 1),
            "tradingDay": _utc_day(),
            "paperTrades": len(closed_trades),
            "wins": len(wins),
            "losses": len(losses),
            "winRate": round((len(wins) / len(closed_trades)) * 100, 2) if closed_trades else 0.0,
            "grossProfit": round(gross_profit, 2),
            "grossLoss": round(gross_loss, 2),
            "netPnl": round(net, 2),
            "profitPct": round(profit_pct, 2),
            "lossPct": round(loss_pct, 2),
            "profitFactor": round(gross_profit / gross_loss, 3) if gross_loss else round(gross_profit, 3),
            "consecutiveLosses": consecutive_losses,
            "closedTrades": [trade.to_dict() for trade in closed_trades[-50:]],
        }

    def evaluate_rotation(self, session_report: dict[str, Any], session_adj: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.paper_session_rotation_enabled:
            return {"shouldRotate": False}
        session_adj = session_adj or {}
        consecutive = int(session_report.get("consecutiveLosses") or 0)
        net = float(session_report.get("netPnl") or 0)
        profit_pct = float(session_report.get("profitPct") or 0)
        loss_amount = abs(net) if net < 0 else 0.0
        profit_target_pct = float(session_adj.get("sessionProfitStopPct") or self.settings.paper_daily_profit_stop_pct)
        max_loss_amount = float(self.settings.paper_max_daily_loss_amount or 0)
        day_agg = self.day_aggregate()
        day_net = float(day_agg.get("netPnl") or 0)
        day_loss = abs(day_net) if day_net < 0 else 0.0
        capital = float(self.settings.trading_capital_default or 0)
        day_loss_pct = day_loss / capital * 100 if capital > 0 and day_net < 0 else 0.0
        max_loss_pct = float(self.settings.paper_max_daily_loss_pct)

        if max_loss_amount > 0 and day_loss >= max_loss_amount:
            return {
                "shouldRotate": False,
                "dailyHalt": True,
                "reason": f"daily paper loss ₹{day_loss:,.0f} >= ₹{max_loss_amount:,.0f}",
            }
        if day_loss_pct >= max_loss_pct:
            return {
                "shouldRotate": False,
                "dailyHalt": True,
                "reason": f"daily paper loss {day_loss_pct:.2f}% >= {max_loss_pct:.2f}%",
            }
        if consecutive >= int(self.settings.paper_max_consecutive_losses):
            return {
                "shouldRotate": True,
                "endReason": "CONSECUTIVE_LOSSES",
                "reason": f"{consecutive} consecutive losses in session",
            }
        if profit_pct >= profit_target_pct:
            return {
                "shouldRotate": True,
                "endReason": "PROFIT_TARGET",
                "reason": f"session profit {profit_pct:.2f}% >= {profit_target_pct:.2f}%",
                "profitTargetPct": profit_target_pct,
            }
        if max_loss_amount > 0 and loss_amount >= max_loss_amount:
            return {
                "shouldRotate": True,
                "endReason": "SESSION_LOSS_LIMIT",
                "reason": f"session loss ₹{loss_amount:,.0f} >= ₹{max_loss_amount:,.0f}",
            }
        return {"shouldRotate": False}

    def status_payload(self, session_report: dict[str, Any]) -> dict[str, Any]:
        completed = self.completed_today()
        return {
            "rotationEnabled": self.settings.paper_session_rotation_enabled,
            "currentSession": {**self.current(), "report": session_report},
            "completedSessionsToday": completed[-20:],
            "dayAggregate": self.day_aggregate(),
            "totalCompletedSessions": len(self._completed),
        }

    def _append_file(self, session: dict[str, Any]) -> None:
        path_value = self.settings.paper_sessions_file
        if not path_value:
            return
        try:
            path = Path(path_value)
            path.parent.mkdir(parents=True, exist_ok=True)
            session_id = str(session.get("id") or "")
            if path.exists() and session_id:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(existing, dict) and str(existing.get("id") or "") == session_id:
                        return
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(session, separators=(",", ":")) + "\n")
            limit = max(100, int(self.settings.paper_sessions_persist_limit))
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > limit:
                path.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
        except Exception:
            return

    def _load_file(self) -> None:
        if PaperSessionManager._file_loaded:
            return
        path_value = self.settings.paper_sessions_file
        if not path_value:
            PaperSessionManager._file_loaded = True
            return
        path = Path(path_value)
        if not path.exists():
            PaperSessionManager._file_loaded = True
            return
        try:
            seen_ids = {str(session.get("id") or "") for session in self._completed}
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue
                session_id = str(item.get("id") or "")
                if session_id and session_id in seen_ids:
                    continue
                self._completed.append(item)
                if session_id:
                    seen_ids.add(session_id)
        except Exception:
            return
        PaperSessionManager._file_loaded = True

    def list_sessions(self, limit: int = 50) -> dict[str, Any]:
        items = self._dedupe_sessions(list(self._completed))[-limit:]
        return {
            "count": len(items),
            "sessions": items,
            "current": self.current(),
            "dayAggregate": self.day_aggregate(),
        }
