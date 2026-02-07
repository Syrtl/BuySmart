"""Smoke tests for recommender and related services."""

import pytest
from backend.services.recommender import parse_intent, recommend
from backend.services.llm_recommender import recommend_from_catalog
from backend.services.tco import compute_tco, value_score
from backend.services.explain import build_why


def test_parse_intent_budget():
    intent = parse_intent("office chair under $200")
    assert intent["budget"] == 200.0

    intent2 = parse_intent("something under 150")
    assert intent2["budget"] == 150.0


def test_parse_intent_category():
    intent = parse_intent("need a good chair for the office")
    assert intent["category"] == "furniture"


def test_recommend_returns_list():
    products = [
        {"id": "1", "title": "Office Chair", "description": "Ergonomic office chair", "price": 199, "category": "furniture"},
        {"id": "2", "title": "Desk Lamp", "description": "LED desk lamp", "price": 39, "category": "furniture"},
    ]
    scored = recommend("comfortable office chair", products, k=2)
    assert len(scored) <= 2
    assert all(isinstance(p[0], dict) and isinstance(p[1], (int, float)) for p in scored)


def test_compute_tco():
    assert compute_tco(100, 5) == 20.0
    assert compute_tco(50, 10) == 5.0


def test_value_score():
    s = value_score(100, 0.8, 5)
    assert 0 <= s <= 1.0


def test_build_why():
    product = {"title": "Chair", "price": 150, "category": "furniture", "quality_score": 0.9, "lifespan_years": 8}
    why = build_why("chair under 200", product, 0.85, {"budget": 200})
    assert "Chair" in why or "150" in why or "furniture" in why


def test_assistant_price_query_prefers_closest_price():
    products = [
        {"id": "w1", "title": "Watch A", "description": "basic watch", "price": 199.0},
        {"id": "w2", "title": "Watch B", "description": "premium watch", "price": 249.0},
        {"id": "w3", "title": "Watch C", "description": "luxury watch", "price": 319.0},
    ]
    out = recommend_from_catalog("250 dollars", products, k=2)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "w2"


def test_assistant_keyword_query_matches_description():
    products = [
        {"id": "p1", "title": "Sport Watch", "description": "durable and waterproof for swimming", "price": 120.0},
        {"id": "p2", "title": "Classic Watch", "description": "leather strap", "price": 110.0},
    ]
    out = recommend_from_catalog("waterproof", products, k=2)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "p1"


def test_assistant_keyword_query_returns_only_keyword_matches_when_available():
    products = [
        {"id": "f1", "title": "Fitness Tracker Pro", "description": "heart rate fitness watch", "price": 149.0},
        {"id": "f2", "title": "Fitness Band Lite", "description": "fitness goals tracking", "price": 89.0},
        {"id": "m1", "title": "Medical Watch", "description": "health monitoring", "price": 129.0},
    ]
    out = recommend_from_catalog("fitness", products, k=5)
    recs = out.get("recommendations") or []
    assert recs
    assert {r.get("id") for r in recs}.issubset({"f1", "f2"})


def test_assistant_brand_plus_feature_prefers_feature_match():
    products = [
        {"id": "a1", "title": "Apple Watch Series 9", "description": "classic everyday smart watch", "price": 199.0},
        {"id": "a2", "title": "Apple Watch Ultra", "description": "sport fitness gps waterproof", "price": 249.0},
        {"id": "a3", "title": "Apple Watch SE", "description": "entry level", "price": 179.0},
    ]
    out = recommend_from_catalog("apple sports watch under $300", products, k=3)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "a2"


def test_assistant_explains_why_top_pick_matches_prompt():
    products = [
        {"id": "c1", "title": "Classic Analog Watch", "description": "classic style leather strap everyday wear", "price": 95.0},
        {"id": "c2", "title": "Sport Fitness Watch", "description": "sport design", "price": 99.0},
    ]
    out = recommend_from_catalog("I need a classic watch under 100 dollars", products, k=2)
    recs = out.get("recommendations") or []
    assert recs
    bullets = " ".join(recs[0].get("score_explanation") or []).lower()
    assert "consultant verdict" in bullets
    assert ("price" in bullets) or ("budget" in bullets)
    assert "requested keywords" not in bullets
    assert "matches 1/" not in bullets
    assert "feature-level matches" not in bullets


def test_assistant_prioritizes_requested_product_type():
    products = [
        {
            "id": "p1",
            "title": "Phone Max 6.5",
            "description": "smartphone with 256gb storage and strong battery",
            "price": 699.0,
            "rating": 4.5,
            "reviews_count": 980,
        },
        {
            "id": "h1",
            "title": "Comfort Headphones",
            "description": "wireless headphones, comfortable fit, good battery",
            "price": 149.0,
            "rating": 4.8,
            "reviews_count": 4500,
        },
    ]
    out = recommend_from_catalog(
        "I need a phone for daily work, comfortable in hand, budget under 800 dollars",
        products,
        k=2,
    )
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "p1"


def test_assistant_dedupes_duplicate_models():
    products = [
        {"id": "d1", "title": "Phone Lite", "description": "smartphone 128gb", "price": 199.0},
        {"id": "d1", "title": "Phone Lite", "description": "smartphone 128gb with extra details", "price": 199.0},
        {"id": "d2", "title": "Phone Pro", "description": "smartphone 256gb", "price": 249.0},
    ]
    out = recommend_from_catalog("need a phone under 300 dollars", products, k=5)
    recs = out.get("recommendations") or []
    ids = [r.get("id") for r in recs]
    assert len(ids) == len(set(ids))


def test_assistant_price_range_prefers_items_inside_range():
    products = [
        {"id": "r1", "title": "Chair Budget", "description": "ergonomic chair", "price": 30.0},
        {"id": "r2", "title": "Chair Mid", "description": "ergonomic chair", "price": 150.0},
        {"id": "r3", "title": "Chair Upper", "description": "ergonomic chair", "price": 190.0},
    ]
    out = recommend_from_catalog("I need an ergonomic chair from 100 to 200 dollars", products, k=2)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] in {"r2", "r3"}


def test_assistant_prefers_feature_coverage_over_brand_only_match():
    products = [
        {"id": "b1", "title": "Apple Watch Series", "description": "brand-only listing", "price": 105.0},
        {"id": "b2", "title": "XFit Active", "description": "waterproof fitness watch with gps", "price": 99.0},
        {"id": "b3", "title": "Generic Watch", "description": "waterproof watch", "price": 98.0},
    ]
    out = recommend_from_catalog("I need an Apple waterproof fitness watch around 100 dollars", products, k=3)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "b2"


def test_assistant_prefers_evidence_over_marketing_title():
    products = [
        {"id": "m1", "title": "Most Comfortable Chair Ever", "description": "Best premium chair for everyone", "price": 119.0},
        {
            "id": "m2",
            "title": "Ergonomic Office Chair",
            "description": "Lumbar support, adjustable armrests, mesh back, BIFMA certified",
            "price": 129.0,
            "rating": 4.6,
            "reviews_count": 3200,
        },
    ]
    out = recommend_from_catalog("I need a comfortable chair for back support under 150 dollars", products, k=2)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "m2"


def test_assistant_respects_budget_in_long_prompt():
    products = [
        {"id": "u1", "title": "Comfort Underwear Pro", "description": "cotton breathable underwear", "price": 29.0},
        {"id": "u2", "title": "Daily Underwear", "description": "soft cotton and modal blend", "price": 18.0},
        {"id": "u3", "title": "Gym Underwear", "description": "moisture-wicking and breathable", "price": 19.0},
    ]
    out = recommend_from_catalog("We want very comfortable underwear, but price should be lower than $20", products, k=3)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] in {"u2", "u3"}
    assert all((r.get("price") or 0) <= 20.0 for r in recs)


def test_assistant_downweights_tiny_review_sample():
    products = [
        {
            "id": "r1",
            "title": "Sports Earbuds Elite",
            "description": "secure fit, sweat resistant, 5.0/5",
            "price": 59.0,
            "rating": 5.0,
            "reviews_count": 10,
        },
        {
            "id": "r2",
            "title": "Sports Earbuds Active",
            "description": "secure fit, sweat resistant, 4.6/5",
            "price": 62.0,
            "rating": 4.6,
            "reviews_count": 2400,
        },
    ]
    out = recommend_from_catalog("Need gym earbuds with secure fit", products, k=2)
    recs = out.get("recommendations") or []
    assert recs
    assert recs[0]["id"] == "r2"
