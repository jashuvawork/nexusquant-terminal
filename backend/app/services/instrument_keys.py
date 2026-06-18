"""Resolve NIFTY / SENSEX / BANKNIFTY constituent symbols to Upstox ISIN instrument keys."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "equity_instrument_keys.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    with _DATA_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def index_constituent_symbols(index: str) -> list[str]:
    idx = index.upper()
    lists = _catalog().get("indexLists") or {}
    return list(lists.get(idx) or lists.get("NIFTY") or [])


def resolve_instrument_key(symbol: str) -> str | None:
    sym = symbol.strip().upper()
    return (_catalog().get("symbols") or {}).get(sym)


def resolve_instrument_keys(symbols: list[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        key = resolve_instrument_key(symbol)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys


def resolve_config_instrument_list(instruments: list[str]) -> list[str]:
    """Map legacy NSE_EQ|SYMBOL entries to Upstox ISIN instrument keys."""
    resolved: list[str] = []
    seen: set[str] = set()
    for item in instruments:
        key = item.strip()
        if not key:
            continue
        if key.upper().startswith("NSE_EQ|") and "|INE" not in key.upper():
            symbol = key.split("|", 1)[1]
            mapped = resolve_instrument_key(symbol)
            if mapped:
                key = mapped
        if key not in seen:
            resolved.append(key)
            seen.add(key)
    return resolved


def display_symbol(instrument_key: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    for field in ("trading_symbol", "symbol"):
        value = str(payload.get(field) or "").strip().upper()
        if value and value not in {"NA", "N/A"}:
            return value
    token = str(payload.get("instrument_token") or instrument_key)
    tail = token.split("|")[-1].split(":")[-1]
    if tail.startswith("INE"):
        # Reverse lookup display alias (e.g. TMPV shown as TATAMOTORS when configured)
        aliases = _catalog().get("aliases") or {}
        for display, actual in aliases.items():
            catalog_key = (_catalog().get("symbols") or {}).get(actual)
            if catalog_key and catalog_key.split("|")[-1] == tail:
                return display
    return tail
