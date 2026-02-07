"""Best-time-to-buy heuristics using price history and seasonal deal windows."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.services.price_history import get_price_history


class DealWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Literal["Black Friday", "Prime Day", "Holiday/New Year", "Memorial Day", "Labor Day", "Back to School", "Unknown"]
    approx_date_range: str = Field(..., alias="approxDateRange")
    expected_discount_range_pct: tuple[int, int] = Field(..., alias="expectedDiscountRangePct")


class BestTimeToBuyResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    recommendation: Literal["buy_now", "wait"]
    confidence: Literal["low", "medium", "high"]
    current_price: float = Field(..., alias="currentPrice")
    low30: float
    low90: float
    delta_from_low30_pct: float = Field(..., alias="deltaFromLow30Pct")
    delta_from_low90_pct: float = Field(..., alias="deltaFromLow90Pct")
    trend: Literal["down", "flat", "up"]
    next_deal_window: DealWindow = Field(..., alias="nextDealWindow")
    explanation: list[str]


_CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "electronics": {"tv", "monitor", "laptop", "gpu", "graphics card", "console", "phone", "smartphone", "tablet", "macbook"},
    "furniture": {"chair", "desk", "sofa", "couch", "table", "stool"},
    "home": {"mattress", "bed", "pillow", "bedding"},
    "audio": {"headphones", "headphone", "earbuds", "earbud", "speaker", "soundbar"},
    "appliances": {"microwave", "blender", "air fryer", "fridge", "refrigerator", "toaster", "washer", "dryer"},
}

_CATEGORY_ALIASES: dict[str, str] = {
    "electronics": "electronics",
    "electronic": "electronics",
    "furniture": "furniture",
    "home": "home",
    "household": "home",
    "audio": "audio",
    "appliances": "appliances",
    "appliance": "appliances",
}

_WINDOWS: dict[str, dict[str, Any]] = {
    "Prime Day": {"start": (7, 8), "end": (7, 18), "discount": (10, 25)},
    "Black Friday": {"start": (11, 20), "end": (11, 30), "discount": (15, 35)},
    "Holiday/New Year": {"start": (12, 20), "end": (1, 10), "discount": (5, 20)},
    "Memorial Day": {"start": (5, 20), "end": (5, 31), "discount": (10, 25)},
    "Labor Day": {"start": (8, 25), "end": (9, 10), "discount": (10, 25)},
    "Back to School": {"start": (8, 1), "end": (9, 15), "discount": (5, 20)},
}

_CATEGORY_WINDOWS: dict[str, list[str]] = {
    "electronics": ["Prime Day", "Black Friday", "Holiday/New Year"],
    "furniture": ["Memorial Day", "Labor Day", "Black Friday"],
    "home": ["Memorial Day", "Labor Day", "Black Friday", "Holiday/New Year"],
    "audio": ["Prime Day", "Black Friday", "Holiday/New Year"],
    "appliances": ["Black Friday", "Holiday/New Year", "Memorial Day"],
    "unknown": ["Black Friday"],
}


def _normalize_category(value: str | None) -> str:
    if not value:
        return "unknown"
    key = str(value).strip().lower()
    return _CATEGORY_ALIASES.get(key, key if key in _CATEGORY_WINDOWS else "unknown")


def _infer_category(title: str | None, category_hint: str | None) -> str:
    normalized_hint = _normalize_category(category_hint)
    if normalized_hint != "unknown":
        return normalized_hint

    text = (title or "").strip().lower()
    if not text:
        return "unknown"
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return category
    return "unknown"


def _window_occurrence(name: str, year: int) -> tuple[date, date]:
    cfg = _WINDOWS[name]
    sm, sd = cfg["start"]
    em, ed = cfg["end"]
    if (sm, sd) <= (em, ed):
        return date(year, sm, sd), date(year, em, ed)
    return date(year, sm, sd), date(year + 1, em, ed)


def _format_range(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}"
    return f"{start.strftime('%b %d, %Y')} - {end.strftime('%b %d, %Y')}"


def _choose_next_window(category: str, today: date) -> DealWindow:
    candidates: list[tuple[date, date, str]] = []
    for name in _CATEGORY_WINDOWS.get(category, _CATEGORY_WINDOWS["unknown"]):
        for yr in (today.year - 1, today.year, today.year + 1):
            start, end = _window_occurrence(name, yr)
            if end >= today:
                candidates.append((start, end, name))
    if not candidates:
        return DealWindow(name="Unknown", approxDateRange="Unknown", expectedDiscountRangePct=(5, 15))

    candidates.sort(key=lambda x: (x[0], x[1]))
    active = [c for c in candidates if c[0] <= today <= c[1]]
    if active:
        start, end, name = active[0]
    else:
        future = [c for c in candidates if c[0] >= today]
        if not future:
            return DealWindow(name="Unknown", approxDateRange="Unknown", expectedDiscountRangePct=(5, 15))
        start, end, name = future[0]

    days_until = (start - today).days
    if days_until > 210:
        return DealWindow(name="Unknown", approxDateRange="Unknown", expectedDiscountRangePct=(5, 15))

    discount = _WINDOWS.get(name, {}).get("discount", (5, 15))
    return DealWindow(name=name, approxDateRange=_format_range(start, end), expectedDiscountRangePct=(int(discount[0]), int(discount[1])))


def _trend_from_points(prices: list[float]) -> Literal["down", "flat", "up"]:
    if len(prices) < 4:
        if len(prices) < 2:
            return "flat"
        delta = (prices[-1] - prices[-2]) / max(prices[-2], 1e-6)
        if delta <= -0.01:
            return "down"
        if delta >= 0.01:
            return "up"
        return "flat"

    recent_avg = sum(prices[-2:]) / 2.0
    prior_avg = sum(prices[-4:-2]) / 2.0
    if prior_avg <= 0:
        return "flat"
    diff_pct = ((recent_avg / prior_avg) - 1.0) * 100.0
    if diff_pct <= -1.0:
        return "down"
    if diff_pct >= 1.0:
        return "up"
    return "flat"


def _confidence(
    recommendation: str,
    trend: str,
    delta30: float,
    delta90: float,
    category: str,
    window_name: str,
) -> Literal["low", "medium", "high"]:
    conf: Literal["low", "medium", "high"] = "medium"
    if recommendation == "buy_now":
        if delta30 <= 2.0 and trend != "down":
            conf = "high"
        elif delta30 > 8.0:
            conf = "low"
    else:
        if delta90 >= 15.0 or (trend == "down" and delta90 >= 8.0):
            conf = "high"
        elif delta90 < 5.0 and trend == "flat":
            conf = "low"
    if category == "unknown" and conf == "high":
        conf = "medium"
    if window_name == "Unknown":
        conf = "low"
    return conf


def analyze_best_time_to_buy(
    *,
    data_dir,
    catalogs: dict[str, list[dict[str, Any]]],
    product_id: str,
    current_price_hint: float | None = None,
    title: str | None = None,
    category_hint: str | None = None,
) -> BestTimeToBuyResponse:
    history = get_price_history(
        data_dir=data_dir,
        catalogs=catalogs,
        product_id=product_id,
        weeks=13,
        current_price_hint=current_price_hint,
    )
    prices = [float(p.price) for p in history.points]
    if not prices:
        prices = [100.0]

    current_price = float(current_price_hint) if current_price_hint is not None else float(prices[-1])
    low30 = min(prices[-4:]) if len(prices) >= 4 else min(prices)
    low90 = min(prices)
    delta30 = ((current_price / max(low30, 1e-6)) - 1.0) * 100.0
    delta90 = ((current_price / max(low90, 1e-6)) - 1.0) * 100.0
    trend = _trend_from_points(prices)

    if current_price <= low30 * 1.05:
        recommendation: Literal["buy_now", "wait"] = "buy_now"
    elif current_price >= low90 * 1.15:
        recommendation = "wait"
    else:
        if trend == "down":
            recommendation = "wait"
        elif trend in {"up", "flat"} and current_price <= low30 * 1.10:
            recommendation = "buy_now"
        else:
            recommendation = "wait"

    category = _infer_category(title, category_hint)
    today = datetime.now(timezone.utc).date()
    next_window = _choose_next_window(category, today)
    confidence = _confidence(recommendation, trend, delta30, delta90, category, next_window.name)

    explanation = [
        f"Current price is ${current_price:.2f}; 30-day low is ${low30:.2f} ({delta30:.1f}% above low).",
        f"90-day low is ${low90:.2f} ({delta90:.1f}% from that low).",
        f"Recent 2-week direction looks {trend}.",
    ]
    if next_window.name != "Unknown":
        explanation.append(
            f"Likely next deal window for {category}: {next_window.name} ({next_window.approx_date_range}), typical {next_window.expected_discount_range_pct[0]}-{next_window.expected_discount_range_pct[1]}%."
        )
    else:
        explanation.append("No clear near-term seasonal event was detected for this category.")
    explanation.append(
        "Recommendation is heuristic, based on recent price position and seasonal category patterns."
    )

    return BestTimeToBuyResponse(
        productId=str(product_id),
        recommendation=recommendation,
        confidence=confidence,
        currentPrice=round(current_price, 2),
        low30=round(low30, 2),
        low90=round(low90, 2),
        deltaFromLow30Pct=round(delta30, 2),
        deltaFromLow90Pct=round(delta90, 2),
        trend=trend,
        nextDealWindow=next_window,
        explanation=explanation[:5],
    )
