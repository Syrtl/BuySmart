"""Deterministic ranking engine tests."""

from backend.services.ranking_engine import parse_intent_payload, rank_products


def test_parse_intent_payload_budget_and_materials():
    intent = parse_intent_payload("Need comfortable cotton underwear under $20")
    assert intent["budget"] == 20.0
    assert "cotton" in intent["materials"]


def test_rank_products_returns_breakdown_fields():
    products = [
        {
            "id": "p1",
            "title": "Sport Watch",
            "description": "waterproof fitness watch with heart rate",
            "category": "electronics",
            "price": 99.0,
            "rating": 4.6,
            "reviews_count": 2400,
        },
        {
            "id": "p2",
            "title": "Classic Watch",
            "description": "classic leather watch",
            "category": "electronics",
            "price": 129.0,
            "rating": 4.2,
            "reviews_count": 350,
        },
        {
            "id": "p3",
            "title": "Budget Watch",
            "description": "basic model",
            "category": "electronics",
            "price": 49.0,
            "rating": 3.8,
            "reviews_count": 140,
        },
    ]

    out = rank_products(user_text="fitness watch under 120", products=products, k=5)
    recs = out.get("recommendations") or []
    assert recs
    first = recs[0]
    assert {"qualityScore", "priceFitScore", "requirementMatch", "materialScore", "totalScore", "flags"}.issubset(set(first.keys()))
    assert 3 <= len(recs) <= 5


def test_rank_products_handles_missing_fields_without_crash():
    products = [
        {"id": "x1", "title": "Unknown Item", "price": 80.0},
        {"id": "x2", "title": "No Price Item", "price": None, "rating": None, "reviews_count": None},
        {"id": "x3", "title": "Fallback Item", "price": 120.0},
    ]
    out = rank_products(user_text="chair around 100", products=products, k=5)
    recs = out.get("recommendations") or []
    assert recs
    assert all("totalScore" in item for item in recs)
