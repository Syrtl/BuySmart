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
_MAX_WEEK_HISTORY = 104
_CACHE_SCHEMA_VERSION = 2
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


_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "electronics": ("tv", "monitor", "laptop", "gpu", "graphics", "console", "phone", "smartphone", "tablet", "camera", "macbook"),
    "furniture": ("chair", "desk", "sofa", "couch", "table", "stool"),
    "home": ("mattress", "bed", "bedding", "pillow", "blanket", "linen"),
    "audio": ("headphones", "earbuds", "earbud", "speaker", "soundbar", "headset"),
    "appliances": ("microwave", "blender", "air fryer", "fridge", "refrigerator", "toaster", "washer", "dryer"),
}


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
    return raw if isinstance(raw, dict) else {}


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
    out: list[str] = []
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


def _guess_category(title: str | None, category_hint: str | None) -> str:
    hint = str(category_hint or "").strip().lower()
    if hint in _CATEGORY_KEYWORDS:
        return hint
    text = str(title or "").strip().lower()
    if not text:
        return "unknown"
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "unknown"


def _resolve_product_context(
    product_id: str,
    catalogs: dict[str, list[dict[str, Any]]],
    current_price_hint: float | None,
    title_hint: str | None,
    category_hint: str | None,
) -> tuple[float, str, str]:
    baseline = _safe_float(current_price_hint)
    resolved_title = str(title_hint or "").strip()
    resolved_category = str(category_hint or "").strip()

    pid = str(product_id).strip().lower()
    for products in catalogs.values():
        for product in products:
            if str(product.get("id", "")).strip().lower() != pid:
                continue
            if baseline is None:
                price = _safe_float(product.get("price"))
                if price is not None and price > 0:
                    baseline = price
            if not resolved_title:
                resolved_title = str(product.get("title") or "").strip()
            if not resolved_category:
                resolved_category = str(product.get("category") or "").strip()
            break

    if baseline is None or baseline <= 0:
        baseline = 100.0

    category = _guess_category(resolved_title, resolved_category)
    return float(baseline), resolved_title, category


def _weekly_anchor(today: date) -> date:
    # Use Sunday as an anchor to keep consistent weekly points.
    return today - timedelta(days=(today.weekday() + 1) % 7)


def _thanksgiving(year: int) -> date:
    nov1 = date(year, 11, 1)
    days_to_thursday = (3 - nov1.weekday()) % 7
    first_thursday = nov1 + timedelta(days=days_to_thursday)
    return first_thursday + timedelta(weeks=3)


def _window_range(name: str, year: int) -> tuple[date, date]:
    if name == "Black Friday/Cyber Monday":
        thanksgiving = _thanksgiving(year)
        return thanksgiving + timedelta(days=1), thanksgiving + timedelta(days=4)
    if name == "Prime Day":
        return date(year, 7, 8), date(year, 7, 15)
    if name == "Holiday/New Year":
        return date(year, 12, 20), date(year + 1, 1, 5)
    if name == "Back to School":
        return date(year, 8, 1), date(year, 8, 31)
    raise ValueError(f"Unknown sale window: {name}")


def _sale_intensity(name: str, category: str) -> tuple[float, float] | None:
    if name == "Black Friday/Cyber Monday":
        if category == "electronics":
            return (0.10, 0.35)
        return (0.05, 0.20)
    if name == "Prime Day":
        if category in {"electronics", "audio"}:
            return (0.05, 0.25)
        return None
    if name == "Holiday/New Year":
        return (0.03, 0.15)
    if name == "Back to School":
        if category == "electronics":
            return (0.03, 0.12)
        return None
    return None


def _build_sale_effects(dates: list[date], category: str, rng: random.Random) -> tuple[dict[int, float], set[int]]:
    effects: dict[int, float] = {}
    dip_indexes: set[int] = set()
    if not dates:
        return effects, dip_indexes

    start_year = min(d.year for d in dates) - 1
    end_year = max(d.year for d in dates) + 1
    windows = ("Black Friday/Cyber Monday", "Prime Day", "Holiday/New Year", "Back to School")

    for year in range(start_year, end_year + 1):
        for window_name in windows:
            dip_range = _sale_intensity(window_name, category)
            if dip_range is None:
                continue

            window_start, window_end = _window_range(window_name, year)
            idxs = [idx for idx, dt in enumerate(dates) if window_start <= dt <= window_end]
            if not idxs:
                continue

            dip_pct = rng.uniform(dip_range[0], dip_range[1])
            for idx in idxs:
                dip_indexes.add(idx)
                effects[idx] = effects.get(idx, 0.0) - dip_pct * rng.uniform(0.82, 1.0)

            rebound_weeks = rng.randint(2, 4)
            rebound_total = dip_pct * rng.uniform(0.45, 0.80)
            weight_total = sum(rebound_weeks - step + 1 for step in range(1, rebound_weeks + 1))
            base_idx = idxs[-1]
            for step in range(1, rebound_weeks + 1):
                idx = base_idx + step
                if idx >= len(dates):
                    break
                weight = (rebound_weeks - step + 1) / max(weight_total, 1)
                effects[idx] = effects.get(idx, 0.0) + (rebound_total * weight * rng.uniform(0.85, 1.1))

    return effects, dip_indexes


def _label_for_index(index: int, total: int) -> str:
    if index == total - 1:
        return "Now"
    weeks_ago = max(1, total - index)
    return f"{weeks_ago}w ago"


def _generate_full_history(
    *,
    product_id: str,
    baseline_price: float,
    category: str,
) -> list[PricePoint]:
    baseline = max(1.0, float(baseline_price))
    floor = max(1.0, baseline * 0.60)
    ceil = max(floor + 0.01, baseline * 1.40)

    today = date.today()
    anchor = _weekly_anchor(today)
    dates = [anchor - timedelta(weeks=(_MAX_WEEK_HISTORY - 1 - idx)) for idx in range(_MAX_WEEK_HISTORY)]

    seed = int(hashlib.sha1(str(product_id).encode("utf-8", errors="replace")).hexdigest()[:16], 16)
    rng = random.Random(seed)

    annual_trend = rng.uniform(-0.03, 0.10)
    weekly_trend = annual_trend / 52.0

    sale_effects, dip_indexes = _build_sale_effects(dates, category, rng)

    value = baseline * (1.0 + rng.uniform(-0.05, 0.05))
    value = max(floor, min(ceil, value))

    points: list[PricePoint] = []
    for idx, point_date in enumerate(dates):
        noise = rng.triangular(-0.02, 0.02, 0.0)
        if abs(noise) < 0.005:
            noise = 0.005 if noise >= 0 else -0.005

        mean_reversion = ((baseline - value) / baseline) * 0.10
        delta_pct = weekly_trend + noise + mean_reversion + sale_effects.get(idx, 0.0)

        if idx in dip_indexes:
            delta_pct = min(delta_pct, -0.03)
            delta_pct = max(delta_pct, -0.35)
        else:
            delta_pct = max(-0.07, min(0.07, delta_pct))

        value = value * (1.0 + delta_pct)
        value = max(floor, min(ceil, value))

        points.append(
            PricePoint(
                label=_label_for_index(idx, _MAX_WEEK_HISTORY),
                date=point_date.isoformat(),
                price=round(value, 2),
            )
        )

    return points


def _slice_history(points: list[PricePoint], weeks: int) -> list[PricePoint]:
    tail = points[-weeks:]
    sliced: list[PricePoint] = []
    total = len(tail)
    for idx, point in enumerate(tail):
        sliced.append(PricePoint(label=_label_for_index(idx, total), date=point.date, price=point.price))
    return sliced


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


def _is_baseline_compatible(cached_baseline: float | None, current_baseline: float) -> bool:
    if cached_baseline is None or cached_baseline <= 0:
        return True
    delta = abs(current_baseline - cached_baseline) / cached_baseline
    return delta <= 0.10


def get_price_history(
    *,
    data_dir: Path,
    catalogs: dict[str, list[dict[str, Any]]],
    product_id: str,
    weeks: int = 13,
    current_price_hint: float | None = None,
    title_hint: str | None = None,
    category_hint: str | None = None,
) -> PriceHistoryResponse:
    normalized_product_id = str(product_id or "").strip()
    normalized_weeks = max(1, min(int(weeks), _MAX_WEEK_HISTORY))
    baseline_price, title, category = _resolve_product_context(
        normalized_product_id,
        catalogs,
        current_price_hint,
        title_hint,
        category_hint,
    )

    cache_path = data_dir / _CACHE_FILE_NAME
    now = _utc_now()
    period_key = f"{normalized_weeks}w"

    with _cache_lock:
        cache_root = _read_cache(cache_path)
        weekly_root = cache_root.get("priceHistoryWeekly")
        if not isinstance(weekly_root, dict):
            weekly_root = {}
        by_product = weekly_root.get(normalized_product_id)
        if not isinstance(by_product, dict):
            by_product = {}

        entry: dict[str, Any] | None = None
        candidate = by_product.get(period_key)
        if isinstance(candidate, dict):
            entry = candidate
        else:
            legacy = by_product.get(str(normalized_weeks))
            if isinstance(legacy, dict):
                entry = legacy

        if entry is not None:
            updated_at = _parse_iso(entry.get("updatedAt"))
            cached_weeks = int(entry.get("weeks") or 0)
            points = _points_from_cache(entry.get("points"), normalized_weeks)
            cached_baseline = _safe_float(entry.get("baseline"))
            schema_version = int(entry.get("schemaVersion") or 0)
            if (
                updated_at is not None
                and (now - updated_at).total_seconds() <= _CACHE_TTL_SECONDS
                and cached_weeks == normalized_weeks
                and points is not None
                and _is_baseline_compatible(cached_baseline, baseline_price)
                and schema_version == _CACHE_SCHEMA_VERSION
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

        full_points = _generate_full_history(
            product_id=normalized_product_id,
            baseline_price=baseline_price,
            category=category,
        )
        points = _slice_history(full_points, normalized_weeks)
        last_updated = _to_iso_z(now)

        by_product[period_key] = {
            "schemaVersion": _CACHE_SCHEMA_VERSION,
            "updatedAt": last_updated,
            "currency": "USD",
            "weeks": normalized_weeks,
            "baseline": round(baseline_price, 4),
            "category": category,
            "title": title,
            "points": [p.model_dump() for p in points],
        }

        # Store 104w once so the analysis endpoint can reuse the same stable history.
        if normalized_weeks != _MAX_WEEK_HISTORY:
            by_product["104w"] = {
                "schemaVersion": _CACHE_SCHEMA_VERSION,
                "updatedAt": last_updated,
                "currency": "USD",
                "weeks": _MAX_WEEK_HISTORY,
                "baseline": round(baseline_price, 4),
                "category": category,
                "title": title,
                "points": [p.model_dump() for p in full_points],
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
