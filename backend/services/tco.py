"""TCO (Total Cost of Ownership) and value scoring."""


def compute_tco(price: float, lifespan_years: float) -> float:
    """Annualized total cost: price / lifespan_years. Higher lifespan = lower TCO."""
    if lifespan_years is None or lifespan_years <= 0:
        return price
    return price / lifespan_years


def value_score(price: float, quality_score: float, lifespan_years: float = 1.0) -> float:
    """
    Value score: higher quality and longer lifespan per dollar is better.
    Returns a score in [0, 1] range; higher = better value.
    """
    if price is None or price <= 0:
        return 0.0
    if quality_score is None:
        quality_score = 0.5
    if lifespan_years is None or lifespan_years <= 0:
        lifespan_years = 1.0
    annual_tco = compute_tco(price, lifespan_years)
    value = quality_score * (lifespan_years / price) * 100
    return min(1.0, max(0.0, value * 0.15))
