"""Price history generation and cache helpers."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_CACHE_TTL_SECONDS = 24 * 60 * 60
_CACHE_FILE_NAME = "price_history_cache.json"
_cache_lock = Lock()


class PricePoint(BaseModel):
    date: str
    price: float


class PriceHistoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    currency: str
    days: int
    points: list[PricePoint]
    min: float
    max: float
    current: float
    last_updated: str = Field(..., alias="lastUpdated")
    source: Literal["mock", "cached"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso_z(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().replace(",", "")
    except Exception:
        return None
    if not text:
        return None
    out = []
    dot_used = False
    for ch in text:
        if ch.isdigit():
            out.append(ch)
            continue
        if ch == "." and not dot_used:
            out.append(ch)
            dot_used = True
            continue
        if out:
            break
    if not out:
        return None
    try:
        return float("".join(out))
    except Exception:
        return None


def _resolve_current_price(product_id: str, catalogs: dict[str, list[dict[str, Any]]], current_price_hint: float | None) -> float:
    hint = _safe_float(current_price_hint)
    if hint is not None and hint > 0:
        return hint
    pid = str(product_id).strip().lower()
    for products in catalogs.values():
        for product in products:
            if str(product.get("id", "")).strip().lower() != pid:
                continue
            price = _safe_float(product.get("price"))
            if price is not None and price > 0:
                return price
    return 100.0


def _generate_points(product_id: str, days: int, current_price: float) -> list[PricePoint]:
    today = date.today()
    days = max(1, int(days))
    current_price = max(1.0, float(current_price))
    floor = max(1.0, current_price * 0.75)
    ceil = max(floor + 0.01, current_price * 1.25)

    seed_key = f"{product_id}:{today.isoformat()}:{current_price:.2f}:{days}"
    seed = int(hashlib.sha1(seed_key.encode("utf-8", errors="replace")).hexdigest()[:16], 16)
    rng = random.Random(seed)

    start = today - timedelta(days=days - 1)
    value = max(floor, min(ceil, current_price * (0.95 + (rng.random() * 0.1))))
    points: list[PricePoint] = []

    for i in range(days):
        day = start + timedelta(days=i)
        mean_reversion = (current_price - value) * 0.10
        daily_noise = rng.uniform(-current_price * 0.012, current_price * 0.012)
        spike = 0.0
        if rng.random() < 0.05:
            spike = rng.uniform(-current_price * 0.04, current_price * 0.04)
        value = max(floor, min(ceil, value + mean_reversion + daily_noise + spike))
        points.append(PricePoint(date=day.isoformat(), price=round(value, 2)))

    return points


def get_price_history(
    *,
    data_dir: Path,
    catalogs: dict[str, list[dict[str, Any]]],
    product_id: str,
    days: int = 90,
    current_price_hint: float | None = None,
) -> PriceHistoryResponse:
    normalized_product_id = str(product_id or "").strip()
    normalized_days = max(1, min(int(days), 365))
    cache_path = data_dir / _CACHE_FILE_NAME
    now = _utc_now()

    with _cache_lock:
        cache_root = _read_cache(cache_path)
        entries = cache_root.get("priceHistory")
        if not isinstance(entries, dict):
            entries = {}

        entry = entries.get(normalized_product_id)
        if isinstance(entry, dict):
            updated_at = _parse_iso(entry.get("updatedAt"))
            cached_days = int(entry.get("days") or 0)
            cached_points_raw = entry.get("points")
            if (
                updated_at is not None
                and (now - updated_at).total_seconds() <= _CACHE_TTL_SECONDS
                and cached_days == normalized_days
                and isinstance(cached_points_raw, list)
                and len(cached_points_raw) == normalized_days
            ):
                points: list[PricePoint] = []
                for raw in cached_points_raw:
                    if not isinstance(raw, dict):
                        continue
                    d = str(raw.get("date") or "").strip()
                    p = _safe_float(raw.get("price"))
                    if not d or p is None:
                        continue
                    points.append(PricePoint(date=d, price=round(p, 2)))
                if len(points) == normalized_days:
                    prices = [p.price for p in points]
                    return PriceHistoryResponse(
                        productId=normalized_product_id,
                        currency=str(entry.get("currency") or "USD"),
                        days=normalized_days,
                        points=points,
                        min=round(min(prices), 2),
                        max=round(max(prices), 2),
                        current=round(points[-1].price, 2),
                        lastUpdated=_to_iso_z(updated_at),
                        source="cached",
                    )

        current_price = _resolve_current_price(normalized_product_id, catalogs, current_price_hint)
        points = _generate_points(normalized_product_id, normalized_days, current_price)
        last_updated = _to_iso_z(now)

        entries[normalized_product_id] = {
            "updatedAt": last_updated,
            "currency": "USD",
            "days": normalized_days,
            "points": [p.model_dump() for p in points],
        }
        cache_root["priceHistory"] = entries
        _write_cache(cache_path, cache_root)

        prices = [p.price for p in points]
        return PriceHistoryResponse(
            productId=normalized_product_id,
            currency="USD",
            days=normalized_days,
            points=points,
            min=round(min(prices), 2),
            max=round(max(prices), 2),
            current=round(points[-1].price, 2),
            lastUpdated=last_updated,
            source="mock",
        )
