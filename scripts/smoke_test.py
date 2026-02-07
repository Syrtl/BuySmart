#!/usr/bin/env python3
"""Smoke test: GET /health (< 5s), POST /recommend, POST /assistant/recommend. Run from repo root.
Usage: python3 scripts/smoke_test.py [base_url]
   or: RAILWAY_URL=https://your-app.up.railway.app python3 scripts/smoke_test.py
"""

import json
import os
import sys
import hashlib
from pathlib import Path

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:
    from urllib2 import Request, urlopen, HTTPError, URLError

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RAILWAY_URL") or "").rstrip("/") or "http://localhost:8000"
TIMEOUT = 15
TIMEOUT_HEALTH = 5  # /health must respond within 5s
TIMEOUT_RECOMMEND = 30
FIXTURE_PATH = Path(__file__).resolve().parent / "page_catalog_override_fixture.json"


def safe_preview(value):
    return str(value or "").replace("\n", " ").strip()[:200]


def _post(path, body, timeout=None):
    t = timeout if timeout is not None else TIMEOUT
    req = Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    r = urlopen(req, timeout=t)
    return json.loads(r.read().decode())


def _post_with_status(path, body, timeout=None):
    t = timeout if timeout is not None else TIMEOUT
    req = Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urlopen(req, timeout=t)
        raw = r.read().decode()
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = None
        return r.getcode(), data, raw
    except HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = None
        return e.code, data, raw


def _get_with_status(path, timeout=None):
    t = timeout if timeout is not None else TIMEOUT
    req = Request(BASE + path, method="GET")
    try:
        r = urlopen(req, timeout=t)
        raw = r.read().decode()
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = None
        return r.getcode(), data, raw
    except HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = None
        return e.code, data, raw


def _normalized_override_ids(items):
    out = set()
    for raw in (items or [])[:60]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        url = str(raw.get("url") or "").strip()
        rid = str(raw.get("id") or "").strip()
        if not rid:
            rid = hashlib.sha1((url + title).encode("utf-8", errors="replace")).hexdigest()[:16]
        out.add(rid)
    return out


def main():
    print("Smoke test for ProcureWise API")
    print("Base URL:", BASE)
    print()

    # GET /health
    try:
        r = urlopen(BASE + "/health", timeout=TIMEOUT_HEALTH)
        health = json.loads(r.read().decode())
        print("GET /health:", health)
    except (HTTPError, URLError, OSError) as e:
        print("GET /health FAILED:", e)
        if isinstance(e, HTTPError) and getattr(e, "code", None) == 502:
            print("Hint: Check Railway logs and set DISABLE_EMBEDDINGS=1")
        print("Start the backend with: ./scripts/run_backend.sh")
        return 1

    failed = 0

    # 0) /api/price-history weekly mode: 200, 13 points, required fields, and cache source on repeated call
    try:
        status1, data1, raw1 = _get_with_status("/api/price-history?productId=demo-asin-1&weeks=13&currentPrice=199.99", timeout=TIMEOUT)
        status2, data2, raw2 = _get_with_status("/api/price-history?productId=demo-asin-1&weeks=13&currentPrice=199.99", timeout=TIMEOUT)
        if status1 != 200:
            print("FAIL: /api/price-history first call — expected 200, got", status1, safe_preview(raw1))
            failed += 1
        elif status2 != 200:
            print("FAIL: /api/price-history second call — expected 200, got", status2, safe_preview(raw2))
            failed += 1
        else:
            points = (data1 or {}).get("points") or []
            required = ("productId", "currency", "weeks", "points", "min", "max", "current", "lastUpdated", "source")
            missing_fields = [k for k in required if k not in (data1 or {})]
            if missing_fields:
                print("FAIL: /api/price-history — missing fields:", missing_fields)
                failed += 1
            elif len(points) != 13:
                print("FAIL: /api/price-history — expected 13 points, got", len(points))
                failed += 1
            elif not all(isinstance(p, dict) and "label" in p and "date" in p and "price" in p for p in points):
                print("FAIL: /api/price-history — points missing label/date/price fields")
                failed += 1
            elif (data2 or {}).get("source") != "cached":
                print("FAIL: /api/price-history — expected second call source='cached', got", (data2 or {}).get("source"))
                failed += 1
            else:
                print("PASS: /api/price-history — returns 13-week series and cached replay")
    except Exception as e:
        print("FAIL: /api/price-history —", e)
        failed += 1

    # 0b) backward compatibility: days=90 should still return weekly payload
    try:
        status, data, raw = _get_with_status("/api/price-history?productId=demo-asin-2&days=90&currentPrice=129.99", timeout=TIMEOUT)
        if status != 200:
            print("FAIL: /api/price-history days=90 compatibility — expected 200, got", status, safe_preview(raw))
            failed += 1
        elif (data or {}).get("weeks") != 13:
            print("FAIL: /api/price-history days=90 compatibility — expected weeks=13, got", (data or {}).get("weeks"))
            failed += 1
        elif len((data or {}).get("points") or []) != 13:
            print("FAIL: /api/price-history days=90 compatibility — expected 13 points")
            failed += 1
        else:
            print("PASS: /api/price-history days=90 compatibility — mapped to 13 weekly points")
    except Exception as e:
        print("FAIL: /api/price-history days=90 compatibility —", e)
        failed += 1

    # 1) /assistant/recommend "office chair under $150": at least one result contains "chair" (even if over budget); over-budget chair has "Over budget" in score_explanation
    try:
        data = _post("/assistant/recommend", {"user_text": "office chair under $150", "store": "amazon", "k": 5}, timeout=TIMEOUT_RECOMMEND)
        recs = data.get("recommendations") or []
        has_chair = any("chair" in (r.get("title") or "").lower() for r in recs)
        if recs and not has_chair:
            print("FAIL: /assistant/recommend 'office chair under $150' — no result contains 'chair'. Got:", [r.get("title") for r in recs])
            failed += 1
        else:
            over_budget_ok = True
            for r in recs:
                if "chair" in (r.get("title") or "").lower() and (r.get("price") or 0) > 150:
                    bullets = " ".join(r.get("score_explanation") or []).lower()
                    if "over budget" not in bullets:
                        over_budget_ok = False
                        break
            if not over_budget_ok:
                print("FAIL: /assistant/recommend 'office chair under $150' — over-budget chair item missing 'Over budget' in score_explanation")
                failed += 1
            else:
                print("PASS: /assistant/recommend 'office chair under $150' — at least one result contains 'chair'; over-budget marked when applicable")
    except Exception as e:
        print("FAIL: /assistant/recommend 'office chair under $150' —", e)
        failed += 1

    # 2) /recommend "office chair under $150": respects budget or marks over-budget in why (30s timeout + 1 retry for slow model load)
    try:
        data = None
        for attempt in range(2):
            try:
                data = _post("/recommend", {"user_text": "office chair under $150", "store": "amazon", "k": 5}, timeout=TIMEOUT_RECOMMEND)
                break
            except Exception:
                if attempt == 0:
                    continue
                raise
        if data is None:
            raise RuntimeError("request failed after retry")
        over_without_mark = 0
        for item in data:
            price = item.get("price")
            why = (item.get("why") or "").lower()
            if price is not None and price > 150 and "over budget" not in why:
                over_without_mark += 1
        if over_without_mark > 0:
            print("FAIL: /recommend 'office chair under $150' — some over-budget items not marked in why")
            failed += 1
        else:
            print("PASS: /recommend 'office chair under $150' — respects budget or marks over-budget in why")
    except Exception as e:
        print("FAIL: /recommend 'office chair under $150' —", e)
        failed += 1

    # 3) /assistant/recommend "desk lamp under $50": returns a lamp item
    try:
        data = _post("/assistant/recommend", {"user_text": "desk lamp under $50", "store": "amazon", "k": 5}, timeout=TIMEOUT_RECOMMEND)
        recs = data.get("recommendations") or []
        has_lamp = any("lamp" in (r.get("title") or "").lower() for r in recs)
        if recs and not has_lamp:
            print("FAIL: /assistant/recommend 'desk lamp under $50' — no result contains 'lamp'. Got:", [r.get("title") for r in recs])
            failed += 1
        else:
            print("PASS: /assistant/recommend 'desk lamp under $50' — returns a lamp item")
    except Exception as e:
        print("FAIL: /assistant/recommend 'desk lamp under $50' —", e)
        failed += 1

    # 4) /assistant/recommend "chair under $150": at least 1 item with "chair" in title and "Over budget" in score_explanation
    try:
        data = _post("/assistant/recommend", {"user_text": "chair under $150", "store": "amazon", "k": 5}, timeout=TIMEOUT_RECOMMEND)
        recs = data.get("recommendations") or []
        has_chair = any("chair" in (r.get("title") or "").lower() for r in recs)
        if not has_chair and recs:
            print("FAIL: /assistant/recommend 'chair under $150' — no result contains 'chair'. Got:", [r.get("title") for r in recs])
            failed += 1
        elif has_chair:
            over_budget_with_mark = True
            for r in recs:
                if "chair" in (r.get("title") or "").lower() and (r.get("price") or 0) > 150:
                    bullets = " ".join(r.get("score_explanation") or []).lower()
                    if "over budget" not in bullets:
                        over_budget_with_mark = False
                        break
            if not over_budget_with_mark:
                print("FAIL: /assistant/recommend 'chair under $150' — chair item over $150 missing 'Over budget' in score_explanation")
                failed += 1
            else:
                print("PASS: /assistant/recommend 'chair under $150' — at least one chair; over-budget has 'Over budget' in score_explanation")
        else:
            print("PASS: /assistant/recommend 'chair under $150' — no chair in catalog or under budget")
    except Exception as e:
        print("FAIL: /assistant/recommend 'chair under $150' —", e)
        failed += 1

    # 5) /assistant/recommend with catalog_override (3 items): returns only override items; at least one chair
    override_catalog = [
        {"id": "ov1", "title": "Ergonomic Office Chair", "price": 179.99, "url": "https://example.com/chair", "snippet": "Mesh back"},
        {"id": "ov2", "title": "Desk Lamp LED", "price": 29.99, "url": "https://example.com/lamp", "snippet": "Dimmable"},
        {"id": "ov3", "title": "24 inch Monitor", "price": 199.99, "url": "https://example.com/monitor", "snippet": "Full HD"},
    ]
    try:
        data = _post("/assistant/recommend", {
            "user_text": "chair under $150",
            "store": "page",
            "k": 5,
            "catalog_override": override_catalog,
        }, timeout=TIMEOUT_RECOMMEND)
        recs = data.get("recommendations") or []
        override_ids = {str(x["id"]) for x in override_catalog}
        returned_ids = {str(r.get("id", "")) for r in recs}
        if not returned_ids.issubset(override_ids):
            print("FAIL: /assistant/recommend catalog_override — returned ids not subset of override. Got:", returned_ids)
            failed += 1
        elif recs and not any("chair" in (r.get("title") or "").lower() for r in recs):
            print("FAIL: /assistant/recommend catalog_override 'chair under $150' — no chair in results. Got:", [r.get("title") for r in recs])
            failed += 1
        else:
            print("PASS: /assistant/recommend catalog_override — results from override only; chair query returns chair")
    except Exception as e:
        print("FAIL: /assistant/recommend catalog_override —", e)
        failed += 1

    # 6) /assistant/recommend with 25-item catalog_override: non-empty, all ids in override, at least one "chair"
    dummy_25 = [
        {"id": "d%d" % i, "title": t, "price": p, "url": "https://ex.com/%d" % i, "snippet": "s"}
        for i, (t, p) in enumerate([
            ("Office Chair", 120), ("Desk Lamp", 35), ("Monitor 24in", 180), ("Keyboard", 50), ("Mouse", 25),
            ("Chair Ergonomic", 199), ("LED Lamp", 29), ("Desk Stand", 80), ("Webcam", 60), ("Headphones", 90),
            ("Chair Mesh", 150), ("Lamp Desk", 40), ("Monitor 27", 220), ("USB Hub", 30), ("Stand Monitor", 70),
            ("Gaming Chair", 250), ("Lamp Dimmable", 45), ("Desk Mat", 20), ("Cable Box", 15), ("Foot Rest", 28),
            ("Task Chair", 175), ("Clamp Lamp", 32), ("Screen 32", 300), ("Adapter", 18), ("Organizer", 22),
        ])
    ]
    try:
        data = _post("/assistant/recommend", {
            "user_text": "chair under $150",
            "store": "page",
            "k": 5,
            "catalog_override": dummy_25,
        }, timeout=TIMEOUT_RECOMMEND)
        recs = data.get("recommendations") or []
        override_ids_25 = {str(x["id"]) for x in dummy_25}
        returned_ids_25 = {str(r.get("id", "")) for r in recs}
        if not recs:
            print("FAIL: /assistant/recommend 25-item override — recommendations empty")
            failed += 1
        elif not returned_ids_25.issubset(override_ids_25):
            print("FAIL: /assistant/recommend 25-item override — returned ids not in override. Got:", returned_ids_25)
            failed += 1
        elif not any("chair" in (r.get("title") or "").lower() for r in recs):
            print("FAIL: /assistant/recommend 25-item override — no chair in results. Got:", [r.get("title") for r in recs])
            failed += 1
        else:
            print("PASS: /assistant/recommend 25-item override — non-empty, ids in override, chair present")
    except Exception as e:
        print("FAIL: /assistant/recommend 25-item override —", e)
        failed += 1

    # 7) /assistant/recommend with realistic fixture (mixed price strings/null/missing ids): 200, ids subset of normalized override, chair present
    try:
        if not FIXTURE_PATH.exists():
            raise RuntimeError("fixture missing: %s" % FIXTURE_PATH)
        fixture_payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        status, data, raw = _post_with_status("/assistant/recommend", fixture_payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend realistic override — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            normalized_ids = _normalized_override_ids((fixture_payload or {}).get("catalog_override") or [])
            returned_ids = {str(r.get("id", "")) for r in recs}
            if not returned_ids.issubset(normalized_ids):
                print("FAIL: /assistant/recommend realistic override — returned ids not subset of normalized override ids:", returned_ids)
                failed += 1
            elif recs and not any("chair" in (r.get("title") or "").lower() for r in recs):
                print("FAIL: /assistant/recommend realistic override — expected at least one chair result. Got:", [r.get("title") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend realistic override — 200, normalized ids respected, keyword match present")
    except Exception as e:
        print("FAIL: /assistant/recommend realistic override —", e)
        failed += 1

    # 8) /assistant/recommend TV under $200 with Amazon-like scanned catalog: 200, ids subset, tv item present
    try:
        tv_override = [
            {"id": "tv1", "title": "32-inch LED TV", "price": "$179.99", "url": "https://example.com/tv1", "snippet": "Smart TV 32 inch"},
            {"id": "tv2", "title": "24-inch Monitor", "price": "$109.99", "url": "https://example.com/monitor1", "snippet": "Office monitor"},
            {"id": "tv3", "title": "40-inch Smart TV", "price": "$239.00", "url": "https://example.com/tv2", "snippet": "TV over budget"},
            {"id": "tv4", "title": "TV Wall Mount", "price": "$24.50", "url": "https://example.com/mount1", "snippet": "Fits 32-55 inch"},
            {"id": "tv5", "title": "HDMI Cable", "price": None, "url": "https://example.com/cable1", "snippet": "6ft high speed cable"},
        ]
        tv_payload = {"user_text": "TV under $200", "store": "page", "k": 5, "catalog_override": tv_override}
        status, data, raw = _post_with_status("/assistant/recommend", tv_payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend TV override — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            tv_ids = _normalized_override_ids(tv_override)
            returned_ids = {str(r.get("id", "")) for r in recs}
            if not returned_ids.issubset(tv_ids):
                print("FAIL: /assistant/recommend TV override — returned ids not subset of override ids:", returned_ids)
                failed += 1
            elif recs and not any("tv" in (r.get("title") or "").lower() for r in recs):
                print("FAIL: /assistant/recommend TV override — expected TV result. Got:", [r.get("title") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend TV override — 200, override ids respected, TV result present")
    except Exception as e:
        print("FAIL: /assistant/recommend TV override —", e)
        failed += 1

    # 9) /assistant/recommend price-only query: closest price should rank first
    try:
        watch_override = [
            {"id": "wa", "title": "Watch Alpha", "price": 199, "url": "https://example.com/wa", "snippet": "Fitness watch"},
            {"id": "wb", "title": "Watch Beta", "price": 249, "url": "https://example.com/wb", "snippet": "GPS watch"},
            {"id": "wc", "title": "Watch Gamma", "price": 319, "url": "https://example.com/wc", "snippet": "Premium watch"},
        ]
        payload = {"user_text": "250 dollars", "store": "page", "k": 3, "catalog_override": watch_override}
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend price-only override — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend price-only override — empty recommendations")
                failed += 1
            elif recs[0].get("id") != "wb":
                print("FAIL: /assistant/recommend price-only override — closest price item not first. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend price-only override — closest price ranked first")
    except Exception as e:
        print("FAIL: /assistant/recommend price-only override —", e)
        failed += 1

    # 10) /assistant/recommend keyword query should match description/title keywords
    try:
        keyword_override = [
            {"id": "k1", "title": "Sport Watch", "price": 120, "url": "https://example.com/k1", "snippet": "Durable and waterproof for swimming"},
            {"id": "k2", "title": "Classic Watch", "price": 110, "url": "https://example.com/k2", "snippet": "Leather strap"},
            {"id": "k3", "title": "Smart Watch", "price": 140, "url": "https://example.com/k3", "snippet": "Bluetooth notifications"},
        ]
        payload = {"user_text": "waterproof", "store": "page", "k": 3, "catalog_override": keyword_override}
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend keyword override — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend keyword override — empty recommendations")
                failed += 1
            elif recs[0].get("id") != "k1":
                print("FAIL: /assistant/recommend keyword override — waterproof item not first. Got:", [r.get("id") for r in recs])
                failed += 1
            elif any("waterproof" not in (((r.get("title") or "") + " " + " ".join(r.get("score_explanation") or [])).lower()) for r in recs):
                print("FAIL: /assistant/recommend keyword override — contains non-keyword items. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend keyword override — keyword match ranked first")
    except Exception as e:
        print("FAIL: /assistant/recommend keyword override —", e)
        failed += 1

    # 11) /assistant/recommend fitness keyword: only fitness items when matches exist
    try:
        fitness_override = [
            {"id": "f1", "title": "Fitness Tracker Pro", "price": 149, "url": "https://example.com/f1", "snippet": "Heart rate and fitness tracking"},
            {"id": "f2", "title": "Fitness Band Lite", "price": 89, "url": "https://example.com/f2", "snippet": "Daily fitness goals"},
            {"id": "m1", "title": "Medical Watch", "price": 129, "url": "https://example.com/m1", "snippet": "Health monitoring"},
        ]
        payload = {"user_text": "fitness", "store": "page", "k": 5, "catalog_override": fitness_override}
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend fitness override — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            ids = [str(r.get("id", "")) for r in recs]
            if not recs:
                print("FAIL: /assistant/recommend fitness override — empty recommendations")
                failed += 1
            elif not set(ids).issubset({"f1", "f2"}):
                print("FAIL: /assistant/recommend fitness override — non-fitness ids present:", ids)
                failed += 1
            else:
                print("PASS: /assistant/recommend fitness override — only keyword-matching items returned")
    except Exception as e:
        print("FAIL: /assistant/recommend fitness override —", e)
        failed += 1

    # 12) /assistant/recommend conversational prompt: should return relevant watch with explanation
    try:
        prompt_override = [
            {"id": "c1", "title": "Classic Analog Watch", "price": 95, "url": "https://example.com/c1", "snippet": "Classic style, leather strap, everyday wear"},
            {"id": "c2", "title": "Sport Fitness Watch", "price": 99, "url": "https://example.com/c2", "snippet": "Fitness tracking and waterproof design"},
            {"id": "c3", "title": "Luxury Smart Watch", "price": 299, "url": "https://example.com/c3", "snippet": "Premium smart features"},
        ]
        payload = {
            "user_text": "I want a classic watch for daily use with a budget up to 100 dollars.",
            "store": "page",
            "k": 3,
            "catalog_override": prompt_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend conversational prompt — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend conversational prompt — empty recommendations")
                failed += 1
            elif recs[0].get("id") not in {"c1", "c2"}:
                print("FAIL: /assistant/recommend conversational prompt — unexpected top result:", recs[0].get("id"))
                failed += 1
            elif not recs[0].get("score_explanation"):
                print("FAIL: /assistant/recommend conversational prompt — missing explanation bullets")
                failed += 1
            else:
                print("PASS: /assistant/recommend conversational prompt — prompt understood with explanation")
    except Exception as e:
        print("FAIL: /assistant/recommend conversational prompt —", e)
        failed += 1

    # 13) /assistant/recommend brand + feature prompt: feature should dominate over brand-only matches
    try:
        brand_feature_override = [
            {"id": "a1", "title": "Apple Watch Series 9", "price": 199, "url": "https://example.com/a1", "snippet": "Classic everyday smart watch"},
            {"id": "a2", "title": "Apple Watch Ultra", "price": 249, "url": "https://example.com/a2", "snippet": "Sports fitness GPS waterproof"},
            {"id": "a3", "title": "Apple Watch SE", "price": 179, "url": "https://example.com/a3", "snippet": "Entry-level smartwatch"},
        ]
        payload = {
            "user_text": "I need an Apple sports watch under 300 dollars",
            "store": "page",
            "k": 3,
            "catalog_override": brand_feature_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend brand+feature prompt — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend brand+feature prompt — empty recommendations")
                failed += 1
            elif recs[0].get("id") != "a2":
                print("FAIL: /assistant/recommend brand+feature prompt — expected sports model first. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend brand+feature prompt — feature-aware ranking works")
    except Exception as e:
        print("FAIL: /assistant/recommend brand+feature prompt —", e)
        failed += 1

    # 14) /assistant/recommend range prompt: should prioritize items inside requested range
    try:
        range_override = [
            {"id": "r1", "title": "Ergonomic Chair Budget", "price": 30, "url": "https://example.com/r1", "snippet": "Ergonomic chair"},
            {"id": "r2", "title": "Ergonomic Chair Mid", "price": 150, "url": "https://example.com/r2", "snippet": "Ergonomic chair"},
            {"id": "r3", "title": "Ergonomic Chair Upper", "price": 190, "url": "https://example.com/r3", "snippet": "Ergonomic chair"},
        ]
        payload = {
            "user_text": "I need an ergonomic chair from 100 to 200 dollars",
            "store": "page",
            "k": 3,
            "catalog_override": range_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend range prompt — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend range prompt — empty recommendations")
                failed += 1
            elif recs[0].get("id") not in {"r2", "r3"}:
                print("FAIL: /assistant/recommend range prompt — expected in-range item first. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend range prompt — in-range items are prioritized")
    except Exception as e:
        print("FAIL: /assistant/recommend range prompt —", e)
        failed += 1

    # 15) /assistant/recommend should prefer concrete evidence over marketing-only title
    try:
        evidence_override = [
            {
                "id": "ev1",
                "title": "Most Comfortable Chair Ever",
                "price": 119,
                "url": "https://example.com/ev1",
                "snippet": "Best premium chair for everyone",
            },
            {
                "id": "ev2",
                "title": "Ergonomic Office Chair",
                "price": 129,
                "url": "https://example.com/ev2",
                "snippet": "Lumbar support, adjustable armrests, mesh back, BIFMA certified, rating 4.6/5, 3200 reviews",
                "rating": 4.6,
                "reviews_count": 3200,
            },
        ]
        payload = {
            "user_text": "I need a comfortable chair for back support under 150 dollars",
            "store": "page",
            "k": 2,
            "catalog_override": evidence_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend evidence ranking — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend evidence ranking — empty recommendations")
                failed += 1
            elif recs[0].get("id") != "ev2":
                print("FAIL: /assistant/recommend evidence ranking — expected evidence-based item first. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend evidence ranking — concrete product signals prioritized")
    except Exception as e:
        print("FAIL: /assistant/recommend evidence ranking —", e)
        failed += 1

    # 16) /assistant/recommend long prompt must honor budget constraint
    try:
        long_budget_override = [
            {"id": "u1", "title": "Comfort Underwear Pro", "price": 29, "url": "https://example.com/u1", "snippet": "cotton breathable underwear"},
            {"id": "u2", "title": "Daily Underwear", "price": 18, "url": "https://example.com/u2", "snippet": "soft cotton and modal blend"},
            {"id": "u3", "title": "Gym Underwear", "price": 19, "url": "https://example.com/u3", "snippet": "moisture-wicking and breathable"},
        ]
        payload = {
            "user_text": "We want very comfortable underwear, but price should be lower than $20",
            "store": "page",
            "k": 3,
            "catalog_override": long_budget_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend long budget prompt — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend long budget prompt — empty recommendations")
                failed += 1
            elif any((r.get("price") or 0) > 20 for r in recs):
                print("FAIL: /assistant/recommend long budget prompt — returned over-budget items:", [(r.get("id"), r.get("price")) for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend long budget prompt — budget constraint respected")
    except Exception as e:
        print("FAIL: /assistant/recommend long budget prompt —", e)
        failed += 1

    # 17) /assistant/recommend ratings should account for review volume
    try:
        review_weight_override = [
            {
                "id": "rv1",
                "title": "Sports Earbuds Elite",
                "price": 59,
                "url": "https://example.com/rv1",
                "snippet": "secure fit, sweat resistant, rating 5.0/5, 10 reviews",
                "rating": 5.0,
                "reviews_count": 10,
            },
            {
                "id": "rv2",
                "title": "Sports Earbuds Active",
                "price": 62,
                "url": "https://example.com/rv2",
                "snippet": "secure fit, sweat resistant, rating 4.6/5, 2400 reviews",
                "rating": 4.6,
                "reviews_count": 2400,
            },
        ]
        payload = {
            "user_text": "Need gym earbuds with secure fit",
            "store": "page",
            "k": 2,
            "catalog_override": review_weight_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend review weighting — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend review weighting — empty recommendations")
                failed += 1
            elif recs[0].get("id") != "rv2":
                print("FAIL: /assistant/recommend review weighting — expected high-volume reviewed item first. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                print("PASS: /assistant/recommend review weighting — review volume handled correctly")
    except Exception as e:
        print("FAIL: /assistant/recommend review weighting —", e)
        failed += 1

    # 18) /assistant/recommend must prioritize requested product type (phone vs headphones)
    try:
        type_priority_override = [
            {
                "id": "tp1",
                "title": "Phone Max 6.5",
                "price": 699,
                "url": "https://example.com/tp1",
                "snippet": "smartphone with 256gb storage and strong battery",
                "rating": 4.5,
                "reviews_count": 980,
            },
            {
                "id": "tp2",
                "title": "Comfort Headphones",
                "price": 149,
                "url": "https://example.com/tp2",
                "snippet": "wireless headphones, comfortable fit, good battery",
                "rating": 4.8,
                "reviews_count": 4500,
            },
        ]
        payload = {
            "user_text": "I need a phone for daily work, comfortable in hand, budget under 800 dollars",
            "store": "page",
            "k": 2,
            "catalog_override": type_priority_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend type priority — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            if not recs:
                print("FAIL: /assistant/recommend type priority — empty recommendations")
                failed += 1
            elif recs[0].get("id") != "tp1":
                print("FAIL: /assistant/recommend type priority — expected phone first. Got:", [r.get("id") for r in recs])
                failed += 1
            else:
                text = " ".join(recs[0].get("score_explanation") or []).lower()
                if ("requested keywords" in text) or ("matches 1/" in text) or ("matches 2/" in text):
                    print("FAIL: /assistant/recommend type priority — explanation contains keyword-count phrasing")
                    failed += 1
                else:
                    print("PASS: /assistant/recommend type priority — product type prioritized without keyword-count explanation")
    except Exception as e:
        print("FAIL: /assistant/recommend type priority —", e)
        failed += 1

    # 19) /assistant/recommend should not return duplicate models
    try:
        duplicate_override = [
            {"id": "dup1", "title": "Phone Lite", "price": 199, "url": "https://example.com/dup1", "snippet": "smartphone 128gb"},
            {"id": "dup1", "title": "Phone Lite", "price": 199, "url": "https://example.com/dup1", "snippet": "smartphone 128gb with details"},
            {"id": "dup2", "title": "Phone Pro", "price": 249, "url": "https://example.com/dup2", "snippet": "smartphone 256gb"},
        ]
        payload = {
            "user_text": "Need a phone under 300 dollars",
            "store": "page",
            "k": 5,
            "catalog_override": duplicate_override,
        }
        status, data, raw = _post_with_status("/assistant/recommend", payload, timeout=TIMEOUT_RECOMMEND)
        if status != 200:
            preview = (raw or "").replace("\n", " ")[:200]
            print("FAIL: /assistant/recommend dedupe — expected 200, got", status, preview)
            failed += 1
        else:
            recs = (data or {}).get("recommendations") or []
            ids = [str(r.get("id", "")) for r in recs]
            if len(ids) != len(set(ids)):
                print("FAIL: /assistant/recommend dedupe — duplicate ids in recommendations:", ids)
                failed += 1
            else:
                print("PASS: /assistant/recommend dedupe — no duplicate models returned")
    except Exception as e:
        print("FAIL: /assistant/recommend dedupe —", e)
        failed += 1

    print()
    if failed:
        print("Smoke test: %d FAIL(s)." % failed)
        return 1
    print("Smoke test: all PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
