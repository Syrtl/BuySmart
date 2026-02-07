"""Value-chart quality score tests."""

import backend.services.value_chart as value_chart


def test_normalize_q0_raw_variants():
    assert value_chart._normalize_q0_raw(0.82) == 0.82
    assert value_chart._normalize_q0_raw(82) == 0.82
    assert value_chart._normalize_q0_raw("82") is None
    assert value_chart._normalize_q0_raw(None) is None
    assert value_chart._normalize_q0_raw(140) is None


def test_quality_handles_missing_price_and_rating_without_crash(monkeypatch):
    monkeypatch.setattr(value_chart, "_llm_intrinsic_scores", lambda prepared: {})

    points = value_chart._normalize_points(
        [
            {"id": "x1", "title": "Item One", "price": 120.0, "rating": None, "reviewCount": None},
            {"id": "x2", "title": "Item Two", "price": None, "rating": 4.7, "reviewCount": 300},
        ]
    )

    assert len(points) == 1
    point = points[0]
    assert point.id == "x1"
    assert 0.0 <= point.intrinsic_q0 <= 1.0
    assert point.intrinsic_q0 == 0.5
    assert 0.0 <= point.market_qm <= 1.0
    assert 0.0 <= point.quality_y <= 1.0
    assert set(["Rn", "Nn", "D", "S"]).issubset(set(point.breakdown.keys()))


def test_build_value_chart_survives_llm_error(monkeypatch):
    def _boom(_prepared):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(value_chart, "_llm_intrinsic_scores", _boom)

    out = value_chart.build_value_chart(
        product_id="demo-llm-fail",
        catalogs={},
        current_price_hint=99.0,
        title_hint="Demo Product",
        category_hint="unknown",
    )

    assert out.points
    assert out.optimal_id in {point.id for point in out.points}
    assert all(0.0 <= point.intrinsic_q0 <= 1.0 for point in out.points)
