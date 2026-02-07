"""Buy-timing analysis built on simulated 2-year weekly history."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.services.price_history import get_price_history


class BestWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    approx_date_range: str = Field(..., alias="approxDateRange")
    typical_drop_pct_range: tuple[float, float] = Field(..., alias="typicalDropPctRange")
    avg_discount_pct: float = Field(..., alias="avgDiscountPct")


class WorstWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    approx_date_range: str = Field(..., alias="approxDateRange")
    typical_increase_pct_range: tuple[float, float] = Field(..., alias="typicalIncreasePctRange")
    avg_premium_pct: float = Field(..., alias="avgPremiumPct")


class NextBestWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    start_date: str = Field(..., alias="startDate")
    end_date: str = Field(..., alias="endDate")
    days_until_start: int = Field(..., alias="daysUntilStart")


class BuyTimingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    currency: str
    best_window: BestWindow = Field(..., alias="bestWindow")
    worst_window: WorstWindow = Field(..., alias="worstWindow")
    next_best_window_this_year: NextBestWindow = Field(..., alias="nextBestWindowThisYear")
    explanation: list[str]
    confidence: Literal["low", "medium", "high"]


_WINDOW_ORDER = [
    "Black Friday/Cyber Monday",
    "Prime Day",
    "Holiday/New Year",
    "Back to School",
    "Spring Full-Price (Mar-Apr)",
]
_SALE_WINDOWS = {
    "Black Friday/Cyber Monday",
    "Prime Day",
    "Holiday/New Year",
    "Back to School",
}
_APPROX_RANGE: dict[str, str] = {
    "Black Friday/Cyber Monday": "Thanksgiving Fri–Mon (late Nov)",
    "Prime Day": "Second week of July (estimated)",
    "Holiday/New Year": "Dec 20 – Jan 5",
    "Back to School": "Aug 1 – Aug 31",
    "Spring Full-Price (Mar-Apr)": "Mar 1 – Apr 30",
}


def _to_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except Exception:
        return None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    data = sorted(float(v) for v in values)
    n = len(data)
    mid = n // 2
    if n % 2 == 1:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def _round_pair(values: list[float], clamp_zero: bool = False) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    data = [max(0.0, float(v)) for v in values] if clamp_zero else [float(v) for v in values]
    return (round(min(data), 2), round(max(data), 2))


def _avg(values: list[float], clamp_zero: bool = False) -> float:
    if not values:
        return 0.0
    data = [max(0.0, float(v)) for v in values] if clamp_zero else [float(v) for v in values]
    return round(sum(data) / len(data), 2)


def _thanksgiving(year: int) -> date:
    nov1 = date(year, 11, 1)
    days_to_thursday = (3 - nov1.weekday()) % 7
    first_thursday = nov1 + timedelta(days=days_to_thursday)
    return first_thursday + timedelta(weeks=3)


def _window_bounds(name: str, year: int) -> tuple[date, date]:
    if name == "Black Friday/Cyber Monday":
        thanksgiving = _thanksgiving(year)
        return thanksgiving + timedelta(days=1), thanksgiving + timedelta(days=4)
    if name == "Prime Day":
        return date(year, 7, 8), date(year, 7, 15)
    if name == "Holiday/New Year":
        return date(year, 12, 20), date(year + 1, 1, 5)
    if name == "Back to School":
        return date(year, 8, 1), date(year, 8, 31)
    if name == "Spring Full-Price (Mar-Apr)":
        return date(year, 3, 1), date(year, 4, 30)
    raise ValueError(f"Unknown window name: {name}")


def _next_window_occurrence(name: str, today: date) -> tuple[date, date]:
    start, end = _window_bounds(name, today.year)
    if end < today:
        return _window_bounds(name, today.year + 1)
    return start, end


def _collect_window_stats(points: list[tuple[date, float]]) -> dict[str, dict[str, list[float]]]:
    if not points:
        return {}
    min_year = min(dt.year for dt, _ in points) - 1
    max_year = max(dt.year for dt, _ in points) + 1

    stats: dict[str, dict[str, list[float]]] = {
        name: {"discounts": [], "premiums": []} for name in _WINDOW_ORDER
    }

    for year in range(min_year, max_year + 1):
        for window_name in _WINDOW_ORDER:
            start, end = _window_bounds(window_name, year)
            window_prices = [price for dt, price in points if start <= dt <= end]
            if not window_prices:
                continue

            baseline_start = start - timedelta(weeks=4)
            baseline_end = start - timedelta(days=1)
            baseline_prices = [price for dt, price in points if baseline_start <= dt <= baseline_end]
            if len(baseline_prices) < 2:
                continue

            baseline = _median(baseline_prices)
            if baseline <= 0:
                continue
            window_median = _median(window_prices)
            discount_pct = ((baseline - window_median) / baseline) * 100.0
            premium_pct = ((window_median - baseline) / baseline) * 100.0

            stats[window_name]["discounts"].append(round(discount_pct, 4))
            stats[window_name]["premiums"].append(round(premium_pct, 4))

    return stats


def _choose_best_window(stats: dict[str, dict[str, list[float]]]) -> tuple[str, list[float]]:
    candidates: list[tuple[str, float]] = []
    for name in _WINDOW_ORDER:
        if name not in _SALE_WINDOWS:
            continue
        discounts = stats.get(name, {}).get("discounts") or []
        if not discounts:
            continue
        candidates.append((name, sum(discounts) / len(discounts)))
    if not candidates:
        return ("Unknown", [])
    candidates.sort(key=lambda x: x[1], reverse=True)
    best_name = candidates[0][0]
    return best_name, list(stats.get(best_name, {}).get("discounts") or [])


def _choose_worst_window(stats: dict[str, dict[str, list[float]]]) -> tuple[str, list[float]]:
    candidates: list[tuple[str, float]] = []
    for name in _WINDOW_ORDER:
        premiums = stats.get(name, {}).get("premiums") or []
        if not premiums:
            continue
        candidates.append((name, sum(premiums) / len(premiums)))
    if not candidates:
        return ("Unknown", [])
    candidates.sort(key=lambda x: x[1], reverse=True)
    worst_name = candidates[0][0]
    return worst_name, list(stats.get(worst_name, {}).get("premiums") or [])


def _confidence(best_discounts: list[float], worst_premiums: list[float], best_avg: float, worst_avg: float) -> Literal["low", "medium", "high"]:
    sample_count = min(len(best_discounts), len(worst_premiums))
    if sample_count >= 2 and best_avg >= 8.0 and worst_avg >= 4.0:
        return "high"
    if sample_count >= 1 and best_avg >= 4.0:
        return "medium"
    return "low"


def analyze_buy_timing(
    *,
    data_dir,
    catalogs: dict[str, list[dict[str, Any]]],
    product_id: str,
    current_price_hint: float | None = None,
    title_hint: str | None = None,
    category_hint: str | None = None,
) -> BuyTimingResponse:
    history = get_price_history(
        data_dir=data_dir,
        catalogs=catalogs,
        product_id=product_id,
        weeks=104,
        current_price_hint=current_price_hint,
        title_hint=title_hint,
        category_hint=category_hint,
    )

    points: list[tuple[date, float]] = []
    for point in history.points:
        dt = _to_date(point.date)
        if dt is None:
            continue
        points.append((dt, float(point.price)))
    points.sort(key=lambda x: x[0])

    if not points:
        today = datetime.now(timezone.utc).date()
        unknown_best = BestWindow(name="Unknown", approxDateRange="Unknown", typicalDropPctRange=(0.0, 0.0), avgDiscountPct=0.0)
        unknown_worst = WorstWindow(name="Unknown", approxDateRange="Unknown", typicalIncreasePctRange=(0.0, 0.0), avgPremiumPct=0.0)
        unknown_next = NextBestWindow(name="Unknown", startDate=today.isoformat(), endDate=today.isoformat(), daysUntilStart=0)
        return BuyTimingResponse(
            productId=str(product_id),
            currency="USD",
            bestWindow=unknown_best,
            worstWindow=unknown_worst,
            nextBestWindowThisYear=unknown_next,
            explanation=["Not enough history points to estimate seasonal windows."],
            confidence="low",
        )

    stats = _collect_window_stats(points)
    best_name, best_discounts = _choose_best_window(stats)
    worst_name, worst_premiums = _choose_worst_window(stats)

    best_range = _round_pair(best_discounts, clamp_zero=True)
    best_avg = _avg(best_discounts, clamp_zero=True)
    worst_range = _round_pair(worst_premiums, clamp_zero=True)
    worst_avg = _avg(worst_premiums, clamp_zero=True)

    best_window = BestWindow(
        name=best_name,
        approxDateRange=_APPROX_RANGE.get(best_name, "Unknown"),
        typicalDropPctRange=best_range,
        avgDiscountPct=best_avg,
    )
    worst_window = WorstWindow(
        name=worst_name,
        approxDateRange=_APPROX_RANGE.get(worst_name, "Unknown"),
        typicalIncreasePctRange=worst_range,
        avgPremiumPct=worst_avg,
    )

    today = datetime.now(timezone.utc).date()
    if best_name == "Unknown":
        next_best = NextBestWindow(
            name="Unknown",
            startDate=today.isoformat(),
            endDate=today.isoformat(),
            daysUntilStart=0,
        )
    else:
        start, end = _next_window_occurrence(best_name, today)
        next_best = NextBestWindow(
            name=best_name,
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            daysUntilStart=max(0, (start - today).days),
        )

    current_price = points[-1][1]
    min_price = min(price for _, price in points)
    med_price = _median([price for _, price in points])

    explanation = [
        f"Best historical window in the last 104 weeks: {best_name} (avg discount {best_avg:.1f}% vs 4-week pre-window baseline).",
        f"Most expensive window on average: {worst_name} (avg premium {worst_avg:.1f}% vs baseline).",
        f"Current price is ${current_price:.2f}; 2-year median is ${med_price:.2f}, and 2-year low is ${min_price:.2f}.",
        f"Next upcoming {best_name} window starts {next_best.start_date} and ends {next_best.end_date}.",
    ]

    confidence = _confidence(best_discounts, worst_premiums, best_avg, worst_avg)

    return BuyTimingResponse(
        productId=str(product_id),
        currency=history.currency,
        bestWindow=best_window,
        worstWindow=worst_window,
        nextBestWindowThisYear=next_best,
        explanation=explanation,
        confidence=confidence,
    )
