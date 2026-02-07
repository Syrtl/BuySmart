"""FastAPI app for product recommendations."""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from backend.services.recommender import embeddings_enabled, parse_intent, recommend
from backend.services.explain import build_why
from backend.services.ranking_engine import parse_intent_payload, rank_products
from backend.services.assistant_explain import explain_candidates
from backend.services.price_history import PriceHistoryResponse, get_price_history
from backend.services.buy_timing import BuyTimingResponse, analyze_buy_timing
from backend.services.value_chart import ValueChartResponse, build_value_chart

app = FastAPI(title="ProcureWise API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path(__file__).resolve().parent / "data"
CATALOGS = {}


def _load_catalogs():
    global CATALOGS
    if CATALOGS:
        return
    for name, filename in [("amazon", "amazon_catalog.json"), ("grainger", "grainger_catalog.json")]:
        path = DATA_DIR / filename
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                CATALOGS[name] = json.load(f)
        else:
            CATALOGS[name] = []


class RecommendRequest(BaseModel):
    user_text: str = Field(..., description="Natural language request (e.g. durable chair under $200)")
    store: str = Field(..., description="Store id: amazon or grainger")
    k: int = Field(5, ge=1, le=20, description="Max number of recommendations")


class RecommendItem(BaseModel):
    id: str
    title: str
    price: float
    category: str
    score: float
    why: str


class ProductOverride(BaseModel):
    """Product from page scan. Accepts both `snippet` and `description` keys."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | int | None = None
    title: str | None = None
    price: float | int | str | None = None
    category: str | None = None
    url: str | None = None
    snippet: str | None = Field(default=None, validation_alias=AliasChoices("snippet", "description"))
    rating: float | int | str | None = None
    reviews_count: int | float | str | None = Field(default=None, validation_alias=AliasChoices("reviews_count", "review_count", "reviews"))


_PRICE_PATTERN = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)")


def _parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text:
        return None
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_int_like(value: Any) -> int | None:
    parsed = _parse_price(value)
    if parsed is None:
        return None
    try:
        return int(round(parsed))
    except Exception:
        return None


def _stable_override_id(url: str, title: str) -> str:
    seed = (url + title).encode("utf-8", errors="replace")
    return hashlib.sha1(seed).hexdigest()[:16]


def _normalize_override_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one override item, dropping unusable entries."""
    try:
        item = ProductOverride.model_validate(raw)
    except Exception:
        return None

    title = (item.title or "").strip()
    if not title:
        return None
    url = (item.url or "").strip()
    category = (item.category or "").strip()
    snippet = (item.snippet or "").strip()[:200]
    price = _parse_price(item.price)
    rating = _parse_price(item.rating)
    reviews_count = _parse_int_like(item.reviews_count)
    if rating is not None:
        rating = max(0.0, min(5.0, float(rating)))
    if reviews_count is not None:
        reviews_count = max(0, reviews_count)
    item_id = str(item.id).strip() if item.id is not None else ""
    if not item_id:
        item_id = _stable_override_id(url, title)

    return {
        "id": item_id,
        "title": title,
        "price": price,
        "category": category,
        "description": snippet,
        "url": url or None,
        "rating": rating,
        "reviews_count": reviews_count,
    }


def _normalize_catalog_override(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in raw_items[:60]:
        if not isinstance(raw, dict):
            continue
        item = _normalize_override_item(raw)
        if item is None:
            continue
        normalized.append(item)
    return normalized


class AssistantRecommendRequest(BaseModel):
    user_text: str = Field(..., description="User request (catalog-only recommendations)")
    store: str = Field(..., description="Store id: amazon, grainger, or page")
    k: int = Field(3, ge=1, le=10, description="Number of recommendations")
    catalog_override: list[dict[str, Any]] | None = Field(None, description="Page-scanned catalog; when set, recommend only from these items")


class IntentParseRequest(BaseModel):
    user_text: str = Field(..., alias="userText")


class ExplainCandidate(BaseModel):
    id: str
    title: str
    price: float | None = None
    quality_score: float = Field(0.0, alias="qualityScore")
    price_fit_score: float = Field(0.0, alias="priceFitScore")
    requirement_match: float = Field(0.0, alias="requirementMatch")
    material_score: float = Field(0.0, alias="materialScore")
    total_score: float = Field(0.0, alias="totalScore")
    flags: list[str] = Field(default_factory=list)


class ExplainRequest(BaseModel):
    user_text: str = Field(..., alias="userText")
    intent: dict[str, Any] | None = None
    candidates: list[ExplainCandidate] = Field(default_factory=list)
    selected_id: str | None = Field(None, alias="selectedId")


def _fallback_parsed_request(user_text: str) -> dict[str, Any]:
    intent = parse_intent(user_text)
    return {
        "budget": intent.get("budget"),
        "category": intent.get("category"),
        "must_haves": [],
        "nice_to_haves": (intent.get("keywords") or [])[:10],
    }


def _intent_to_parsed_request(intent: dict[str, Any], user_text: str) -> dict[str, Any]:
    requirements = [str(x) for x in (intent.get("requirements") or []) if str(x).strip()]
    materials = [str(x) for x in (intent.get("materials") or []) if str(x).strip()]
    return {
        "budget": intent.get("budget"),
        "category": intent.get("category"),
        "must_haves": requirements[:10],
        "nice_to_haves": materials[:10],
    }


def _assistant_empty_response(
    user_text: str,
    follow_up_question: str,
    *,
    using_override: bool,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parsed_request": _fallback_parsed_request(user_text),
        "recommendations": [],
        "follow_up_question": follow_up_question,
        "using_override": using_override,
    }
    if error_code or error_message:
        payload["error"] = {
            "code": error_code or "ASSISTANT_RECOMMEND_ERROR",
            "message": error_message or follow_up_question,
        }
    return payload


def _sanitize_assistant_response(result: dict[str, Any] | None, products: list[dict[str, Any]], k: int) -> dict[str, Any] | None:
    """Keep only recommendations that exist in the provided catalog."""
    if not isinstance(result, dict):
        return None
    recs = result.get("recommendations")
    if not isinstance(recs, list):
        return None

    by_id = {str(p.get("id", "")): p for p in products}
    by_title = {}
    for p in products:
        t = (p.get("title") or "").strip().lower()
        if t:
            by_title[t] = p

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        rid = str(rec.get("id", "")).strip()
        product = by_id.get(rid)
        if product is None:
            title_key = str(rec.get("title", "")).strip().lower()
            product = by_title.get(title_key)
            if product is not None:
                rid = str(product.get("id", "")).strip()
        if product is None or not rid or rid in seen_ids:
            continue
        seen_ids.add(rid)

        bullets = rec.get("score_explanation")
        if isinstance(bullets, str):
            bullets = [bullets]
        if not isinstance(bullets, list):
            bullets = []
        bullets = [str(b).strip() for b in bullets if str(b).strip()]
        if not bullets:
            bullets = [f"Title (catalog): {product.get('title', '')}."]

        tco = rec.get("tco")
        if not isinstance(tco, dict):
            tco = {
                "available": False,
                "yearly_cost": None,
                "formula": "null",
                "notes": "Unknown from catalog.",
            }

        unknowns = rec.get("unknowns")
        if not isinstance(unknowns, list):
            unknowns = []

        normalized.append(
            {
                "id": rid,
                "title": str(rec.get("title") or product.get("title") or ""),
                "price": _parse_price(rec.get("price")) if rec.get("price") is not None else _parse_price(product.get("price")),
                "category": str(rec.get("category") or product.get("category") or ""),
                "score_explanation": bullets[:8],
                "tco": tco,
                "unknowns": [str(x) for x in unknowns][:5],
            }
        )
        if len(normalized) >= k:
            break

    if not normalized:
        return None

    parsed_request = result.get("parsed_request")
    if not isinstance(parsed_request, dict):
        parsed_request = {"budget": None, "category": None, "must_haves": [], "nice_to_haves": []}
    out = {
        "parsed_request": parsed_request,
        "recommendations": normalized,
        "follow_up_question": result.get("follow_up_question"),
    }
    return out


@app.on_event("startup")
def startup():
    _load_catalogs()
    disable_value = os.environ.get("DISABLE_EMBEDDINGS", "0")
    print(f"DISABLE_EMBEDDINGS={disable_value}; embeddings enabled: {'true' if embeddings_enabled() else 'false'}")


@app.get("/")
def root():
    return {
        "service": "ProcureWise API",
        "docs": "/docs",
        "health": "/health",
        "recommend": "POST /recommend",
        "assistant": "POST /assistant/recommend",
        "intent": "POST /api/intent",
        "explain": "POST /api/explain",
        "price_history": "GET /api/price-history?productId=XXX&weeks=13",
        "buy_timing": "GET /api/buy-timing?productId=XXX",
        "value_chart": "GET /api/value-chart?productId=XXX",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/intent")
def parse_intent_endpoint(req: IntentParseRequest):
    user_text = str(req.user_text or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Missing required field: userText")
    intent = parse_intent_payload(user_text)
    return {
        "userText": user_text,
        "intent": intent,
        "parsed_request": _intent_to_parsed_request(intent, user_text),
    }


@app.post("/api/explain")
def explain_endpoint(req: ExplainRequest):
    user_text = str(req.user_text or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Missing required field: userText")
    if not req.candidates:
        raise HTTPException(status_code=400, detail="Missing required field: candidates")

    candidates_payload = [item.model_dump(by_alias=True) for item in req.candidates]
    explanation = explain_candidates(
        user_text=user_text,
        intent=req.intent,
        candidates=candidates_payload,
        selected_id=req.selected_id,
    )
    return {
        "userText": user_text,
        "intent": req.intent,
        "selectedId": explanation.get("selectedId"),
        "summary": explanation.get("summary"),
        "items": explanation.get("items") or [],
        "scoreTable": candidates_payload,
    }


@app.get("/api/price-history", response_model=PriceHistoryResponse)
def price_history_endpoint(
    product_id: str | None = Query(None, alias="productId"),
    weeks: int | None = Query(None, ge=1, le=104),
    days: int | None = Query(None, ge=1, le=365),
    current_price: float | None = Query(None, alias="currentPrice"),
    title: str | None = Query(None),
    category: str | None = Query(None),
):
    normalized_product_id = str(product_id or "").strip()
    if not normalized_product_id:
        raise HTTPException(status_code=400, detail="Missing required query parameter: productId")
    if weeks is not None:
        requested_weeks = weeks
    elif days is not None:
        requested_weeks = max(1, min(104, int(round(float(days) / 7.0))))
    else:
        requested_weeks = 13
    _load_catalogs()
    return get_price_history(
        data_dir=DATA_DIR,
        catalogs=CATALOGS,
        product_id=normalized_product_id,
        weeks=requested_weeks,
        current_price_hint=current_price,
        title_hint=title,
        category_hint=category,
    )


@app.get("/api/buy-timing", response_model=BuyTimingResponse)
def buy_timing_endpoint(
    product_id: str | None = Query(None, alias="productId"),
    current_price: float | None = Query(None, alias="currentPrice"),
    title: str | None = Query(None),
    category: str | None = Query(None),
):
    normalized_product_id = str(product_id or "").strip()
    if not normalized_product_id:
        raise HTTPException(status_code=400, detail="Missing required query parameter: productId")
    _load_catalogs()
    return analyze_buy_timing(
        data_dir=DATA_DIR,
        catalogs=CATALOGS,
        product_id=normalized_product_id,
        current_price_hint=current_price,
        title_hint=title,
        category_hint=category,
    )


@app.get("/api/value-chart", response_model=ValueChartResponse)
def value_chart_endpoint(
    product_id: str | None = Query(None, alias="productId"),
    current_price: float | None = Query(None, alias="currentPrice"),
    title: str | None = Query(None),
    category: str | None = Query(None),
    rating: float | None = Query(None),
    review_count: int | None = Query(None, alias="reviewCount"),
):
    normalized_product_id = str(product_id or "").strip()
    if not normalized_product_id:
        raise HTTPException(status_code=400, detail="Missing required query parameter: productId")
    _load_catalogs()
    return build_value_chart(
        product_id=normalized_product_id,
        catalogs=CATALOGS,
        current_price_hint=current_price,
        title_hint=title,
        category_hint=category,
        rating_hint=rating,
        review_count_hint=review_count,
    )


def _product_to_item(product: dict, score: float, why: str) -> RecommendItem:
    return RecommendItem(
        id=str(product.get("id", "")),
        title=str(product.get("title", "")),
        price=float(product.get("price", 0)),
        category=str(product.get("category", "")),
        score=round(score, 4),
        why=why,
    )


@app.post("/recommend", response_model=list[RecommendItem])
def recommend_endpoint(req: RecommendRequest):
    _load_catalogs()
    store = req.store.lower().strip()
    if store not in CATALOGS:
        raise HTTPException(status_code=400, detail=f"Unknown store: {req.store}. Use 'amazon' or 'grainger'.")
    products = CATALOGS[store]
    if not products:
        return []

    intent = parse_intent(req.user_text)
    try:
        scored = recommend(
            req.user_text,
            products,
            budget=intent.get("budget"),
            category=intent.get("category"),
            k=req.k,
        )
    except Exception:
        scored = [(p, 0.5) for p in products[: req.k]]

    result = []
    for product, score in scored:
        why = build_why(req.user_text, product, score, intent)
        result.append(_product_to_item(product, score, why))
    return result


@app.post("/assistant/recommend")
def assistant_recommend_endpoint(req: AssistantRecommendRequest):
    """Assistant recommendations endpoint."""
    started = time.perf_counter()
    override_raw = req.catalog_override or []
    override_provided = req.catalog_override is not None
    using_override = override_provided
    received_override_count = len(override_raw)
    normalized_count = 0
    try:
        if override_provided:
            products = _normalize_catalog_override(override_raw)
            normalized_count = len(products)
            if not products:
                return _assistant_empty_response(
                    req.user_text,
                    "I couldn't read products from this page. Try scanning a search results page.",
                    using_override=True,
                )
            ranking = rank_products(user_text=req.user_text, products=products, k=req.k)
            return {
                "parsed_request": _intent_to_parsed_request(ranking.get("intent") or {}, req.user_text),
                "intent": ranking.get("intent") or {},
                "recommendations": ranking.get("recommendations") or [],
                "follow_up_question": None,
                "assistant_mode": "deterministic",
                "using_override": True,
            }

        _load_catalogs()
        store = req.store.lower().strip()
        if store == "page":
            return _assistant_empty_response(
                req.user_text,
                "No scanned catalog found. Click 'Scan this page' and try again.",
                using_override=False,
            )
        if store not in CATALOGS:
            raise HTTPException(status_code=400, detail=f"Unknown store: {req.store}. Use 'amazon', 'grainger', or provide catalog_override with store 'page'.")
        products = CATALOGS[store]
        if not products:
            return _assistant_empty_response(
                req.user_text,
                "Catalog is empty for this store.",
                using_override=False,
            )
        ranking = rank_products(user_text=req.user_text, products=products, k=req.k)
        return {
            "parsed_request": _intent_to_parsed_request(ranking.get("intent") or {}, req.user_text),
            "intent": ranking.get("intent") or {},
            "recommendations": ranking.get("recommendations") or [],
            "follow_up_question": None,
            "assistant_mode": "deterministic",
            "using_override": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        err_type = type(exc).__name__
        err_message = str(exc)
        print(f"/assistant/recommend error type={err_type} message={err_message}")
        return _assistant_empty_response(
            req.user_text,
            "I couldn't process this request. Try again in a moment.",
            using_override=using_override,
            error_code="ASSISTANT_RECOMMEND_ERROR",
            error_message=f"{err_type}: {err_message}",
        )
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        print(
            "/assistant/recommend "
            f"using_override={'true' if using_override else 'false'} "
            f"received_override_count={received_override_count} "
            f"normalized_count={normalized_count} "
            f"duration_ms={duration_ms:.0f}"
        )
