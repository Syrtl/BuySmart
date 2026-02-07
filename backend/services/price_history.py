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
    label: str
    date: str
    price: float


class PriceHistoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    currency: str
    weeks: int
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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text:
        return None
    cleaned = text.replace(",", "")
    out = []
    dot_used = False
    for ch in cleaned:
        if ch.isdigit():
            out.append(ch)
        elif ch == "." and not dot_used:
            out.append(ch)
            dot_used = True
        elif out:
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


def _weekly_anchor(today: date) -> date:
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def _planned_sale_effects(weeks: int, rng: random.Random) -> tuple[dict[int, float], set[int]]:
    event_count = 1 if rng.random() < 0.72 else 2
    candidate_weeks = list(range(1, max(2, weeks - 2)))
    rng.shuffle(candidate_weeks)
    starts: list[int] = []
    for idx in candidate_weeks:
        if any(abs(idx - prev) < 3 for prev in starts):
            continue
        starts.append(idx)
        if len(starts) >= event_count:
            break

    effects: dict[int, float] = {}
    intentional_dips: set[int] = set()

    for start in sorted(starts):
        dip_pct = rng.uniform(0.05, 0.15)
        effects[start] = effects.get(start, 0.0) - dip_pct
        intentional_dips.add(start)

        recovery_weeks = 1 if rng.random() < 0.6 else 2
        total_recovery = dip_pct * rng.uniform(0.55, 0.85)
        if recovery_weeks == 1:
            if start + 1 < weeks:
                effects[start + 1] = effects.get(start + 1, 0.0) + total_recovery
        else:
            first = total_recovery * rng.uniform(0.45, 0.65)
            second = total_recovery - first
            if start + 1 < weeks:
                effects[start + 1] = effects.get(start + 1, 0.0) + first
            if start + 2 < weeks:
                effects[start + 2] = effects.get(start + 2, 0.0) + second

    return effects, intentional_dips


def _label_for_offset(weeks_ago: int) -> str:
    if weeks_ago <= 0:
        return "Now"
    return f"{weeks_ago}w ago"


def _generate_weekly_points(product_id: str, weeks: int, current_price: float) -> list[PricePoint]:
    weeks = max(1, int(weeks))
    current_price = max(1.0, float(current_price))
    floor = max(1.0, current_price * 0.75)
    ceil = max(floor + 0.01, current_price * 1.25)

    today = date.today()
    anchor = _weekly_anchor(today)
    seed_key = f"{product_id}:{anchor.isoformat()}:{current_price:.2f}:{weeks}"
    seed = int(hashlib.sha1(seed_key.encode("utf-8", errors="replace")).hexdigest()[:16], 16)
    rng = random.Random(seed)

    effects, intentional_dip_weeks = _planned_sale_effects(weeks, rng)
    value = max(floor, min(ceil, current_price * (0.97 + (rng.random() * 0.06))))
    points: list[PricePoint] = []

    for week_index in range(weeks):
        base_change = rng.uniform(0.005, 0.03)
        direction = -1.0 if rng.random() < 0.5 else 1.0
        drift_pct = direction * base_change
        reversion_pct = ((current_price - value) / current_price) * 0.30
        delta_pct = drift_pct + reversion_pct + effects.get(week_index, 0.0)

        if week_index in intentional_dip_weeks:
            delta_pct = max(delta_pct, -0.20)
        else:
            if delta_pct > 0.07:
                delta_pct = 0.07
            elif delta_pct < -0.07:
                delta_pct = -0.07

        value = value * (1.0 + delta_pct)
        value = max(floor, min(ceil, value))

        weeks_ago = weeks - week_index - 1
        point_date = anchor - timedelta(weeks=weeks_ago)
        points.append(
            PricePoint(
                label=_label_for_offset(weeks_ago),
                date=point_date.isoformat(),
                price=round(value, 2),
            )
        )

    return points


def _points_from_cache(raw_points: Any, expected_count: int) -> list[PricePoint] | None:
    if not isinstance(raw_points, list) or len(raw_points) != expected_count:
        return None
    points: list[PricePoint] = []
    for raw in raw_points:
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label") or "").strip()
        dt = str(raw.get("date") or "").strip()
        price = _safe_float(raw.get("price"))
        if not label or not dt or price is None:
            return None
        points.append(PricePoint(label=label, date=dt, price=round(price, 2)))
    return points


def get_price_history(
    *,
    data_dir: Path,
    catalogs: dict[str, list[dict[str, Any]]],
    product_id: str,
    weeks: int = 13,
    current_price_hint: float | None = None,
) -> PriceHistoryResponse:
    normalized_product_id = str(product_id or "").strip()
    normalized_weeks = max(1, min(int(weeks), 52))
    cache_path = data_dir / _CACHE_FILE_NAME
    now = _utc_now()

    with _cache_lock:
        cache_root = _read_cache(cache_path)
        weekly_root = cache_root.get("priceHistoryWeekly")
        if not isinstance(weekly_root, dict):
            weekly_root = {}
        by_product = weekly_root.get(normalized_product_id)
        if not isinstance(by_product, dict):
            by_product = {}

        entry = by_product.get(str(normalized_weeks))
        if isinstance(entry, dict):
            updated_at = _parse_iso(entry.get("updatedAt"))
            cached_weeks = int(entry.get("weeks") or 0)
            points = _points_from_cache(entry.get("points"), normalized_weeks)
            if (
                updated_at is not None
                and (now - updated_at).total_seconds() <= _CACHE_TTL_SECONDS
                and cached_weeks == normalized_weeks
                and points is not None
            ):
                prices = [p.price for p in points]
                return PriceHistoryResponse(
                    productId=normalized_product_id,
                    currency=str(entry.get("currency") or "USD"),
                    weeks=normalized_weeks,
                    points=points,
                    min=round(min(prices), 2),
                    max=round(max(prices), 2),
                    current=round(points[-1].price, 2),
                    lastUpdated=_to_iso_z(updated_at),
                    source="cached",
                )

        current_price = _resolve_current_price(normalized_product_id, catalogs, current_price_hint)
        points = _generate_weekly_points(normalized_product_id, normalized_weeks, current_price)
        last_updated = _to_iso_z(now)

        by_product[str(normalized_weeks)] = {
            "updatedAt": last_updated,
            "currency": "USD",
            "weeks": normalized_weeks,
            "points": [p.model_dump() for p in points],
        }
        weekly_root[normalized_product_id] = by_product
        cache_root["priceHistoryWeekly"] = weekly_root
        _write_cache(cache_path, cache_root)

        prices = [p.price for p in points]
        return PriceHistoryResponse(
            productId=normalized_product_id,
            currency="USD",
            weeks=normalized_weeks,
            points=points,
            min=round(min(prices), 2),
            max=round(max(prices), 2),
            current=round(points[-1].price, 2),
            lastUpdated=last_updated,
            source="mock",
        )
