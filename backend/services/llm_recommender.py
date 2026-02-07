"""Catalog recommendation logic."""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from backend.services.tco import compute_tco

_COMMON_MISSING_FIELDS = ["warranty", "exact_dimensions", "compatibility_list", "user_ratings_count"]

SYSTEM_PROMPT = """You are ProcureWise Assistant. You recommend products ONLY from the provided catalog.

STRICT RULES:
1) Use ONLY information present in the catalog or in the user request. Never invent product specs, durability, warranties, materials, ratings, or any numbers not in the catalog.
2) If data is missing for a field, respond with "Unknown from catalog" for that field.
3) Ask at most one follow-up question (in follow_up_question). Use null if no follow-up needed.
4) Output ONLY valid JSON in the exact schema below. No markdown, no code fences, no extra text before or after the JSON.
5) Do not use marketing language or free-form fluff. Be factual and brief.
6) For each recommendation: score_explanation must list only catalog facts (or "Unknown from catalog"). tco.yearly_cost only if lifespan_years exists in catalog (formula: price / lifespan_years). unknowns list missing catalog fields.

REQUIRED JSON SCHEMA (output this and nothing else):
{
  "parsed_request": {
    "budget": number or null,
    "category": string or null,
    "must_haves": [string],
    "nice_to_haves": [string]
  },
  "recommendations": [
    {
      "id": "string",
      "title": "string",
      "price": number,
      "category": "string",
      "score_explanation": ["bullet using only catalog facts or Unknown from catalog"],
      "tco": {
        "available": true or false,
        "yearly_cost": number or null,
        "formula": "price / lifespan_years" or "null",
        "notes": "string"
      },
      "unknowns": [string]
    }
  ],
  "follow_up_question": "string or null"
}
"""

DEVELOPER_PROMPT = """Reminder: Only catalog data. No invented facts. Missing data = "Unknown from catalog". One follow-up max. JSON only, no extra text."""

OVERRIDE_SYSTEM_ADDON = """
When using catalog_override:
- Use ONLY the provided catalog items.
- Rank by best fit to the user prompt using title + description/snippet + category + budget/price.
- Do not pick only by brand name when other prompt constraints exist.
- For each recommendation, explain WHY it matches the prompt (keywords/features/budget fit) using catalog facts only.
- If price is missing for an item, say "Unknown from catalog" for that field.
- Return JSON only."""


def _extract_json(raw: str) -> str | None:
    """Extract first JSON object from raw response (strip markdown fences if present)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip() or None


def _validate_schema(obj: Any) -> bool:
    """Return True if obj matches the required assistant response schema."""
    if not isinstance(obj, dict):
        return False
    if "parsed_request" not in obj or "recommendations" not in obj or "follow_up_question" not in obj:
        return False
    pr = obj["parsed_request"]
    if not isinstance(pr, dict) or "budget" not in pr or "category" not in pr or "must_haves" not in pr or "nice_to_haves" not in pr:
        return False
    recs = obj["recommendations"]
    if not isinstance(recs, list):
        return False
    for r in recs:
        if not isinstance(r, dict):
            return False
        required = ("id", "title", "price", "category", "score_explanation", "tco", "unknowns")
        if any(k not in r for k in required):
            return False
        if not isinstance(r.get("score_explanation"), list) or not isinstance(r.get("unknowns"), list):
            return False
        tco = r.get("tco")
        if not isinstance(tco, dict) or "available" not in tco or "yearly_cost" not in tco or "formula" not in tco or "notes" not in tco:
            return False
    if obj["follow_up_question"] is not None and not isinstance(obj["follow_up_question"], str):
        return False
    return True


def recommend_via_llm(
    user_text: str,
    products: list[dict],
    k: int = 3,
    catalog_override: bool = False,
) -> dict[str, Any] | None:
    """
    Call completion API with strict prompts. Return parsed JSON matching schema, or None on failure/invalid response.
    When catalog_override=True, system prompt enforces: use ONLY provided catalog; do not invent; missing price = "Unknown from catalog".
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return None

    try:
        import openai
    except ImportError:
        return None

    catalog_snippet = json.dumps(products[:60], default=str)[:16000]
    user_message = f"""USER_REQUEST:
{user_text}

CATALOG (use only these products; recommend best {k}):
{catalog_snippet}
"""
    system = SYSTEM_PROMPT + (OVERRIDE_SYSTEM_ADDON if catalog_override else "")

    try:
        client = openai.OpenAI(api_key=api_key.strip())
        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": DEVELOPER_PROMPT + "\n\n" + user_message},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return None
        raw_json = _extract_json(content)
        if not raw_json:
            return None
        obj = json.loads(raw_json)
        if not _validate_schema(obj):
            return None
        return obj
    except Exception:
        return None


_PRIMARY_PRODUCT_KEYWORDS = {"chair", "chairs", "desk", "desks", "lamp", "lamps", "monitor", "monitors", "keyboard", "keyboards", "mouse", "headphones", "webcam", "stand", "shelving", "gloves", "helmet", "drill", "fan", "vac", "extinguisher", "boots"}
_STRONG_NOUNS = {"chair", "chairs", "lamp", "lamps", "desk", "desks", "monitor", "monitors"}
_STOPWORDS = {
    "under", "need", "want", "looking", "for", "the", "and", "with", "under", "max", "below", "budget", "office", "under",
    "dollar", "dollars", "usd", "price", "around", "about", "approximately", "approx", "roughly",
    "i", "im", "i'm", "me", "my", "to", "a", "an", "of", "in", "on", "it", "that", "this",
    "buy", "find", "looking", "search", "show", "give", "need", "want", "like", "good", "best",
    "please", "can", "could", "would", "should", "something", "any", "or", "if", "is", "are",
    "from", "between", "up", "than", "less", "more", "no", "within", "range", "for", "use",
    "мне", "нужно", "хочу", "примерно", "около", "до", "от", "для", "и", "в", "на", "по", "это",
    "покажи", "найди", "подбери", "какие", "какой", "нужен", "нужна", "нужны", "можешь",
    "пожалуйста", "бюджет", "цена", "цена$", "диапазон", "между", "или", "если", "чтобы",
}
_NON_DISCRIMINATIVE_KEYWORDS = {"watch", "watches", "smartwatch", "smartwatches", "clock", "clocks", "item", "items", "product", "products", "часы"}
_CATALOG_CATEGORIES = {"furniture", "electronics", "safety", "tools", "storage", "lighting", "electrical", "hvac", "material-handling", "accessories"}
_CONCEPT_TERMS = {
    "comfort": {
        "comfortable", "comfort", "ergonomic", "orthopedic", "lumbar", "support", "cushion", "padded", "mesh",
        "удоб", "эргоном", "ортопед", "поясниц", "поддержк", "мягк",
    },
    "fitness": {
        "fitness", "sport", "sports", "workout", "training", "running", "active", "gym",
        "фитнес", "спорт", "трениров", "бег",
    },
    "waterproof": {
        "waterproof", "water resistant", "water-resistant", "swim", "ip68",
        "водонепроница", "влагозащит", "влагостой",
    },
    "classic_style": {
        "classic", "minimalist", "minimal", "vintage", "analog", "leather", "traditional",
        "классич", "аналог", "кожа", "минимал",
    },
    "health_tracking": {
        "heart", "sleep", "ecg", "spo2", "health", "wellness",
        "пульс", "сон", "здоров", "давлен",
    },
    "battery_life": {
        "battery", "long battery", "long-lasting", "autonomy", "48h", "7-day",
        "батар", "аккумулятор", "автоном",
    },
}
_CONCEPT_LABELS_EN = {
    "comfort": "comfort and ergonomics",
    "fitness": "sports/fitness use",
    "waterproof": "water resistance",
    "classic_style": "classic style",
    "health_tracking": "health tracking",
    "battery_life": "battery life",
}
_CONCEPT_LABELS_RU = {
    "comfort": "комфорт и эргономика",
    "fitness": "спортивный/фитнес формат",
    "waterproof": "влагозащита",
    "classic_style": "классический стиль",
    "health_tracking": "мониторинг здоровья",
    "battery_life": "автономность батареи",
}
_PRODUCT_FAMILIES: dict[str, set[str]] = {
    "phone": {"phone", "smartphone", "mobile phone", "iphone", "android phone", "телефон", "смартфон", "айфон"},
    "headphones": {"headphone", "headphones", "earbuds", "earphones", "headset", "наушники", "гарнитура"},
    "underwear": {"underwear", "briefs", "boxers", "panties", "lingerie", "белье", "трусы"},
    "chair": {"chair", "office chair", "gaming chair", "stool", "кресло", "стул"},
    "watch": {"watch", "smartwatch", "fitness watch", "часы", "смарт часы"},
    "laptop": {"laptop", "notebook", "macbook", "ноутбук"},
    "keyboard": {"keyboard", "mechanical keyboard", "клавиатура"},
    "mouse": {"mouse", "computer mouse", "мышь"},
}
_PRODUCT_FAMILY_LABELS_EN = {
    "phone": "phone",
    "headphones": "headphones",
    "underwear": "underwear",
    "chair": "chair",
    "watch": "watch",
    "laptop": "laptop",
    "keyboard": "keyboard",
    "mouse": "mouse",
}
_PRODUCT_FAMILY_LABELS_RU = {
    "phone": "телефон",
    "headphones": "наушники",
    "underwear": "нижнее белье",
    "chair": "кресло/стул",
    "watch": "часы",
    "laptop": "ноутбук",
    "keyboard": "клавиатура",
    "mouse": "мышь",
}
_CONCRETE_SIGNALS = {
    "comfort": {
        "lumbar", "back support", "ergonomic", "orthopedic", "adjustable", "mesh", "memory foam",
        "seat depth", "armrest", "bifma", "breathable", "cotton", "modal", "bamboo", "spandex",
        "seamless", "moisture-wicking", "поясниц", "эргоном", "ортопед", "регулиру",
        "хлопок", "модал", "бамбук", "эластан", "дышащ", "бесшов",
    },
    "fitness": {
        "secure fit", "sport", "fitness", "lightweight", "sweat", "ipx", "гим", "трениров",
    },
    "waterproof": {
        "ipx", "ip67", "ip68", "waterproof", "water resistant", "swim", "влагозащит", "водонепроница",
    },
    "classic_style": {
        "analog", "leather", "stainless", "minimal", "classic", "кожа", "классич",
    },
    "health_tracking": {
        "heart rate", "ecg", "spo2", "sleep", "sensor", "пульс", "сон",
    },
    "battery_life": {
        "mah", "hours", "hour", "battery", "quick charge", "wireless charging", "час", "батар",
    },
}
_MARKETING_TERMS = {
    "best", "ultimate", "top", "perfect", "amazing", "no.1", "#1", "premium", "unbeatable",
    "самый лучший", "лучший", "топ", "идеальн", "премиум",
}
_SPEC_REGEX = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|mah|w|wh|db|hz|inch|inches|mm|cm|kg|lb|lbs|g|years?|year|ip\d{2})\b|"
    r"\b\d+(?:\.\d+)?\s*(?:ч|час|часов|мАч|дБ|Гц|мм|см|кг|лет)\b)",
    re.IGNORECASE,
)


def _contains_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text or ""))


def _token_present(text: str, token: str) -> bool:
    t = (text or "").lower()
    tok = (token or "").strip().lower()
    if not tok:
        return False
    pattern = r"\b" + re.escape(tok).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, t) is not None


def _detect_requested_product_family(text: str) -> str | None:
    t = (text or "").lower()
    best_family = None
    best_score = 0
    for family, terms in _PRODUCT_FAMILIES.items():
        score = 0
        for term in terms:
            if _token_present(t, term):
                score += 1
        if score > best_score:
            best_score = score
            best_family = family
    return best_family if best_score > 0 else None


def _product_family_score(product: dict, family: str | None) -> int:
    if not family:
        return 0
    text = _product_text(product)
    terms = _PRODUCT_FAMILIES.get(family) or set()
    return 1 if any(_token_present(text, term) for term in terms) else 0


def _product_family_label(family: str | None, language: str) -> str | None:
    if not family:
        return None
    labels = _PRODUCT_FAMILY_LABELS_RU if language == "ru" else _PRODUCT_FAMILY_LABELS_EN
    return labels.get(family, family)


def _format_money(value: float | None) -> str:
    if value is None:
        return "Unknown from catalog"
    return f"${float(value):.2f}"


def _product_text(product: dict) -> str:
    return f"{(product.get('title') or '').lower()} {(product.get('description') or '').lower()} {(product.get('category') or '').lower()}"


def _normalized_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _candidate_richness_score(product: dict) -> float:
    score = 0.0
    if _safe_price(product.get("price")) is not None:
        score += 1.0
    desc = (product.get("description") or "").strip()
    if desc:
        score += min(1.5, len(desc) / 200.0)
    rating = _safe_rating(product)
    if rating is not None:
        score += rating / 5.0
    reviews = _safe_reviews_count(product)
    if reviews is not None and reviews > 0:
        score += min(1.0, math.log10(reviews + 1.0) / 3.0)
    return score


def _dedupe_products(products: list[dict]) -> list[dict]:
    """Drop duplicate models by id/title, keeping the richer entry."""
    selected_by_key: dict[str, tuple[float, dict]] = {}
    order: list[str] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "").strip().lower()
        title = _normalized_title(p.get("title"))
        keys = []
        if pid:
            keys.append(f"id:{pid}")
        if title:
            keys.append(f"title:{title}")
        if not keys:
            continue

        score = _candidate_richness_score(p)
        existing_key = None
        existing_score = None
        existing_product = None
        for key in keys:
            entry = selected_by_key.get(key)
            if entry is not None:
                existing_key = key
                existing_score, existing_product = entry
                break

        chosen_product = p
        chosen_score = score
        if existing_product is not None and existing_score is not None and existing_score >= score:
            chosen_product = existing_product
            chosen_score = existing_score

        for key in keys:
            if key not in selected_by_key:
                order.append(key)
            selected_by_key[key] = (chosen_score, chosen_product)

    out: list[dict] = []
    seen_obj_ids: set[int] = set()
    for key in order:
        entry = selected_by_key.get(key)
        if entry is None:
            continue
        _, product = entry
        oid = id(product)
        if oid in seen_obj_ids:
            continue
        seen_obj_ids.add(oid)
        out.append(product)
    return out


def _safe_rating(product: dict) -> float | None:
    value = product.get("rating")
    if value is not None:
        try:
            rating = float(value)
            if 0.0 <= rating <= 5.0:
                return rating
        except Exception:
            pass
    text = _product_text(product)
    m = re.search(r"(\d(?:\.\d+)?)\s*(?:/5|out of 5|stars?)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        rating = float(m.group(1))
    except Exception:
        return None
    return rating if 0.0 <= rating <= 5.0 else None


def _safe_reviews_count(product: dict) -> int | None:
    value = product.get("reviews_count")
    if value is not None:
        try:
            n = int(float(value))
            if n >= 0:
                return n
        except Exception:
            pass
    text = _product_text(product).replace(",", "")
    m = re.search(r"(\d{1,7})\s*(?:reviews?|ratings?)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except Exception:
        return None
    return n if n >= 0 else None


def _matched_concrete_signals(product: dict, query_concepts: list[str]) -> list[str]:
    text = _product_text(product)
    signals: list[str] = []
    targets = query_concepts if query_concepts else list(_CONCRETE_SIGNALS.keys())
    seen = set()
    for concept in targets:
        for token in (_CONCRETE_SIGNALS.get(concept) or set()):
            tok = token.lower()
            if tok in text and tok not in seen:
                seen.add(tok)
                signals.append(tok)
    return signals[:8]


def _spec_count(product: dict) -> int:
    text = _product_text(product)
    return len(_SPEC_REGEX.findall(text))


def _marketing_hits(product: dict) -> int:
    text = _product_text(product)
    return sum(1 for term in _MARKETING_TERMS if term in text)


def _trust_signal(product: dict) -> float:
    rating = _safe_rating(product)
    reviews = _safe_reviews_count(product)
    if rating is None:
        return 0.0
    review_count = max(0, int(reviews or 0))

    prior_mean = 4.0
    prior_count = 60.0
    weighted_rating = ((rating * review_count) + (prior_mean * prior_count)) / (review_count + prior_count)
    rating_score = max(0.0, min(1.0, (weighted_rating - 3.0) / 2.0))
    volume_score = max(0.0, min(1.0, math.log10(review_count + 1.0) / 3.0))
    return (rating_score * 0.75) + (volume_score * 0.25)


def _evidence_score(product: dict, query_concepts: list[str], active_keywords: list[str]) -> tuple[float, list[str], bool]:
    meaningful_keywords = [kw for kw in active_keywords if kw not in _NON_DISCRIMINATIVE_KEYWORDS]
    has_substantive_intent = bool(query_concepts or meaningful_keywords)
    if not has_substantive_intent:
        return 0.0, [], False

    matched_signals = _matched_concrete_signals(product, query_concepts)
    spec_count = _spec_count(product)
    trust = _trust_signal(product)
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    kw_desc_hits = 0
    for kw in active_keywords:
        if kw and kw in desc:
            kw_desc_hits += 1
        elif kw and kw in title:
            kw_desc_hits += 0.5
    score = 0.0
    score += min(2.5, len(matched_signals) * 0.45)
    score += min(1.5, spec_count * 0.25)
    score += min(1.2, kw_desc_hits * 0.25)
    score += trust * 1.4

    marketing_hits = _marketing_hits(product)
    marketing_only = has_substantive_intent and marketing_hits > 0 and len(matched_signals) == 0 and spec_count == 0
    if marketing_only:
        score -= min(1.0, marketing_hits * 0.35)
    return score, matched_signals, marketing_only


def _detect_query_concepts(user_text: str) -> list[str]:
    text = (user_text or "").lower()
    concepts: list[str] = []
    for key, terms in _CONCEPT_TERMS.items():
        if any(term in text for term in terms):
            concepts.append(key)
    return concepts


def _matched_concepts(product: dict, concepts: list[str]) -> list[str]:
    if not concepts:
        return []
    text = _product_text(product)
    matched: list[str] = []
    for concept in concepts:
        terms = _CONCEPT_TERMS.get(concept) or set()
        if any(term in text for term in terms):
            matched.append(concept)
    return matched


def _concept_match_score(product: dict, concepts: list[str]) -> float:
    if not concepts:
        return 0.0
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    score = 0.0
    for concept in concepts:
        terms = _CONCEPT_TERMS.get(concept) or set()
        title_hit = any(term in title for term in terms)
        desc_hit = any(term in desc for term in terms)
        if title_hit:
            score += 1.6
        elif desc_hit:
            score += 1.0
    return score


def _concept_labels(concepts: list[str], language: str) -> list[str]:
    labels = _CONCEPT_LABELS_RU if language == "ru" else _CONCEPT_LABELS_EN
    return [labels.get(c, c.replace("_", " ")) for c in concepts]


def _buyer_focus_summary(
    parsed: dict[str, Any],
    active_keywords: list[str],
    query_concepts: list[str],
    requested_family: str | None,
    language: str,
) -> str:
    concept_labels = _concept_labels(query_concepts[:3], language)
    family_label = _product_family_label(requested_family, language)
    focus_parts = []
    if family_label:
        focus_parts.append(family_label)
    focus_parts.extend(concept_labels[:2])
    if not focus_parts:
        keyword_labels = [k for k in active_keywords if k not in _NON_DISCRIMINATIVE_KEYWORDS][:2]
        focus_parts.extend(keyword_labels)

    if language == "ru":
        focus = ", ".join(focus_parts) if focus_parts else "практичность и релевантность задаче"
        if parsed.get("min_price") is not None and parsed.get("max_price") is not None:
            price = f"диапазон {_format_money(parsed.get('min_price'))}–{_format_money(parsed.get('max_price'))}"
        elif parsed.get("budget") is not None:
            price = f"бюджет до {_format_money(parsed.get('budget'))}"
        elif parsed.get("target_price") is not None:
            price = f"ориентир по цене около {_format_money(parsed.get('target_price'))}"
        else:
            price = "без жесткого бюджета"
        return f"{focus}; {price}"

    focus = ", ".join(focus_parts) if focus_parts else "practical fit for your use case"
    if parsed.get("min_price") is not None and parsed.get("max_price") is not None:
        price = f"price range {_format_money(parsed.get('min_price'))}-{_format_money(parsed.get('max_price'))}"
    elif parsed.get("budget") is not None:
        price = f"budget up to {_format_money(parsed.get('budget'))}"
    elif parsed.get("target_price") is not None:
        price = f"target around {_format_money(parsed.get('target_price'))}"
    else:
        price = "no strict budget"
    return f"{focus}; {price}"


def _parse_request(text: str) -> dict[str, Any]:
    """Parse user text: budget (numeric only), category, required_keyword, must_haves (no budget tokens), nice_to_haves."""
    out: dict[str, Any] = {
        "budget": None,
        "min_price": None,
        "max_price": None,
        "target_price": None,
        "category": None,
        "required_keyword": None,
        "must_haves": [],
        "nice_to_haves": [],
    }
    if not text or not isinstance(text, str):
        return out
    t = text.lower().strip()
    range_match = re.search(
        r"(?:from|between|от)\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:to|and|до|-)\s*\$?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)",
        t,
        re.IGNORECASE,
    )
    if range_match:
        values = [x for x in range_match.groups() if x]
        if len(values) >= 2:
            a = float(values[0])
            b = float(values[1])
            lo, hi = (a, b) if a <= b else (b, a)
            out["min_price"] = lo
            out["max_price"] = hi
            out["budget"] = hi
            out["target_price"] = (lo + hi) / 2.0

    budget_match = None
    if out["max_price"] is None:
        budget_match = re.search(
            r"(?:under|max|below|up to|budget|no more than|less than|lower than|cheaper than|"
            r"до|не дороже|ниже)\s*\$?\s*(\d+(?:\.\d+)?)|^\$(\d+(?:\.\d+)?)",
            t,
            re.IGNORECASE,
        )
        if budget_match:
            g = budget_match.groups()
            val = float(g[0] or g[1] or 0)
            if val > 0:
                out["budget"] = val
                out["max_price"] = val
                out["target_price"] = val

    if out["min_price"] is None:
        min_match = re.search(
            r"(?:from|at least|min(?:imum)?|not less than|от|минимум|не меньше)\s*\$?\s*(\d+(?:\.\d+)?)",
            t,
            re.IGNORECASE,
        )
        if min_match:
            out["min_price"] = float(min_match.group(1))
            if out["target_price"] is None:
                out["target_price"] = out["min_price"]

    if out["target_price"] is None:
        around_match = re.search(
            r"(?:around|about|roughly|approx(?:imately)?|~|примерно|около)\s*\$?\s*(\d+(?:\.\d+)?)",
            t,
            re.IGNORECASE,
        )
        if around_match:
            out["target_price"] = float(around_match.group(1))
            if out["min_price"] is None and out["max_price"] is None:
                out["min_price"] = out["target_price"] * 0.8
                out["max_price"] = out["target_price"] * 1.2
                out["budget"] = out["max_price"]

    if out["target_price"] is None:
        explicit_price = re.findall(r"(?:\$|usd|dollars?)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:usd|dollars?)", t, re.IGNORECASE)
        explicit_vals = [g[0] or g[1] for g in explicit_price if (g[0] or g[1])]
        if explicit_vals:
            out["target_price"] = float(explicit_vals[-1])
        else:
            generic_vals = re.findall(r"\d+(?:\.\d+)?", t)
            plausible_prices = [float(v) for v in generic_vals if float(v) >= 20.0]
            if plausible_prices:
                out["target_price"] = float(plausible_prices[-1])
    category_map = {
        "chair": "furniture", "desk": "furniture", "furniture": "furniture",
        "keyboard": "electronics", "monitor": "electronics", "electronics": "electronics",
        "lamp": "furniture", "safety": "safety", "gloves": "safety", "helmet": "safety",
        "tools": "tools", "drill": "tools", "storage": "storage", "shelving": "storage",
        "lighting": "lighting", "electrical": "electrical", "hvac": "HVAC",
        "material-handling": "material-handling", "accessories": "accessories",
    }
    for word, cat in category_map.items():
        norm = (cat or "").lower()
        if word in t and norm in _CATALOG_CATEGORIES:
            out["category"] = norm
            break
    words = [w for w in re.split(r"\W+", t) if len(w) > 1 and w not in _STOPWORDS]
    must_keywords = {
        "durable", "cheap", "ergonomic", "heavy", "industrial", "wireless", "led",
        "sport", "sports", "fitness", "waterproof", "classic", "comfort", "comfortable",
        "orthopedic", "lumbar", "battery", "health", "running", "swim",
        "удобный", "эргономичный", "классический", "водонепроницаемый", "спортивный", "фитнес", "ортопедический",
    }
    must_haves = [w for w in words if w in must_keywords and not w.isdigit()]
    for kw in _PRIMARY_PRODUCT_KEYWORDS:
        if kw in t:
            out["required_keyword"] = kw
            break
    nice_to_haves = [w for w in words if w not in must_keywords and not w.isdigit()][:15]
    if not out["required_keyword"] and nice_to_haves:
        for w in nice_to_haves:
            if w in _STRONG_NOUNS:
                out["required_keyword"] = w
                break
    out["must_haves"] = list(dict.fromkeys(must_haves))[:10]
    out["nice_to_haves"] = list(dict.fromkeys(nice_to_haves))[:10]
    if out["category"] is not None and (out["category"] or "").lower() not in _CATALOG_CATEGORIES:
        out["category"] = None
    return out


def _relevance_score(product: dict, parsed: dict[str, Any]) -> float:
    score = 0.0
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    cat = (product.get("category") or "").lower()
    title_tokens = set(re.findall(r"[a-zа-я0-9]+", title))
    desc_tokens = set(re.findall(r"[a-zа-я0-9]+", desc))
    must = set((parsed.get("must_haves") or []))
    nice = set((parsed.get("nice_to_haves") or []))
    for w in must:
        if w in title_tokens:
            score += 0.35
        elif w in desc_tokens:
            score += 0.25
    for w in nice:
        if w in title_tokens:
            score += 0.18
        elif w in desc_tokens:
            score += 0.12
    req_kw = (parsed.get("required_keyword") or "").lower()
    if req_kw:
        if req_kw in title:
            score += 0.35
        elif req_kw in desc:
            score += 0.25
    if parsed.get("category") and cat == parsed["category"].lower():
        score += 0.25
    return min(1.0, score)


def _value_rank_term(product: dict) -> tuple[float, float]:
    price = product.get("price") or 0.01
    if price <= 0:
        price = 0.01
    q = product.get("quality_score") or 0.5
    lifespan = product.get("lifespan_years") or 1.0
    if lifespan <= 0:
        lifespan = 1.0
    value = q / price
    tco_val = compute_tco(price, lifespan)
    return (value, -tco_val)


def _matched_query_terms(product: dict, query_terms: list[str]) -> list[str]:
    text = f"{(product.get('title') or '').lower()} {(product.get('description') or '').lower()} {(product.get('category') or '').lower()}"
    matched = []
    for t in query_terms:
        if t and t in text:
            matched.append(t)
    return matched


def _keyword_weights(keywords: list[str], products: list[dict]) -> dict[str, float]:
    if not keywords:
        return {}
    n = max(1, len(products))
    out = {}
    for kw in keywords:
        kwl = kw.lower()
        df = 0
        for p in products:
            txt = f"{(p.get('title') or '').lower()} {(p.get('description') or '').lower()} {(p.get('category') or '').lower()}"
            if kwl in txt:
                df += 1
        out[kwl] = math.log((n + 1.0) / (df + 1.0)) + 1.0
    return out


def _keyword_match_score(product: dict, keyword_weights: dict[str, float]) -> float:
    if not keyword_weights:
        return 0.0
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    cat = (product.get("category") or "").lower()
    score = 0.0
    for kw, w in keyword_weights.items():
        if kw in title:
            score += w * 1.8
        elif kw in desc:
            score += w * 1.2
        elif kw in cat:
            score += w * 1.0
    return score


def _score_explanation_bullets(
    product: dict,
    budget: float | None = None,
    query_terms: list[str] | None = None,
    target_price: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    matched_concepts: list[str] | None = None,
    total_query_concepts: int = 0,
    max_concept_match_count: int = 0,
    buyer_focus_summary: str | None = None,
    evidence_signals: list[str] | None = None,
    spec_count: int = 0,
    rating: float | None = None,
    reviews_count: int | None = None,
    marketing_only: bool = False,
    requested_family_label: str | None = None,
    family_match_score: int = 0,
    language: str = "en",
    rank: int = 1,
    total_candidates: int = 0,
    is_top_pick: bool = False,
) -> list[str]:
    bullets = []
    price = product.get("price")
    matched_concepts = matched_concepts or []
    evidence_signals = evidence_signals or []
    concept_labels = _concept_labels(matched_concepts, language)

    if language == "ru":
        if is_top_pick:
            focus = buyer_focus_summary or "ваш запрос"
            if total_candidates > 1:
                compared = f"после сравнения {total_candidates} товаров из скана"
            elif total_candidates == 1:
                compared = "по результатам сравнения найденных товаров"
            else:
                compared = "после сравнения товаров из скана"
            bullets.append(f"Консультантский вывод: это лучший выбор {compared}, потому что он ближе всего к вашему запросу ({focus}).")
        else:
            bullets.append(f"Альтернатива №{rank}: хороший вариант, но по приоритетам уступает позиции выше.")

        if requested_family_label and family_match_score > 0:
            bullets.append(f"По типу товара это корректный матч: {requested_family_label}.")

        if total_query_concepts > 0 and matched_concepts:
            bullets.append(f"Закрывает ключевые потребности по запросу: {', '.join(concept_labels[:3])}.")
            if is_top_pick and max_concept_match_count > 0 and len(matched_concepts) == max_concept_match_count:
                bullets.append("По смысловым признакам это один из самых точных матчей в скане.")
    else:
        if is_top_pick:
            focus = buyer_focus_summary or "your prompt"
            if total_candidates > 1:
                compared = f"out of {total_candidates} scanned options"
            elif total_candidates == 1:
                compared = "for the only matching scanned option"
            else:
                compared = "across scanned options"
            bullets.append(f"Consultant verdict: this is the best fit {compared} because it aligns most closely with {focus}.")
        else:
            bullets.append(f"Alternative #{rank}: solid match, but slightly weaker than higher-ranked options.")

        if requested_family_label and family_match_score > 0:
            bullets.append(f"Correct product type match: {requested_family_label}.")

        if total_query_concepts > 0 and matched_concepts:
            bullets.append(f"Covers your key needs: {', '.join(concept_labels[:3])}.")
            if is_top_pick and max_concept_match_count > 0 and len(matched_concepts) == max_concept_match_count:
                bullets.append("Among scanned items, it has one of the strongest feature-level matches.")

    if evidence_signals:
        if language == "ru":
            bullets.append(f"Опираюсь на конкретные признаки из карточки, а не на рекламный слоган: {', '.join(evidence_signals[:4])}.")
        else:
            bullets.append(f"Uses concrete listing evidence (not marketing wording): {', '.join(evidence_signals[:4])}.")
    elif spec_count > 0:
        if language == "ru":
            bullets.append(f"В карточке есть конкретные характеристики ({spec_count}), поэтому выбор основан на фактах.")
        else:
            bullets.append(f"The listing includes concrete specs ({spec_count}), so this choice is evidence-based.")
    elif marketing_only:
        if language == "ru":
            bullets.append("В описании много маркетинговых формулировок и мало проверяемых характеристик.")
        else:
            bullets.append("The listing has marketing-heavy wording and limited verifiable details.")

    if rating is not None:
        if reviews_count is not None and reviews_count > 0:
            if language == "ru":
                bullets.append(f"Рейтинг в карточке: {rating:.1f}/5 на основе {reviews_count} отзывов.")
            else:
                bullets.append(f"Listing rating: {rating:.1f}/5 based on {reviews_count} reviews.")
            if reviews_count < 30:
                if language == "ru":
                    bullets.append("Отзывов пока мало, поэтому оценка менее надежна.")
                else:
                    bullets.append("Review sample is still small, so rating confidence is limited.")
        else:
            if language == "ru":
                bullets.append(f"Рейтинг в карточке: {rating:.1f}/5.")
            else:
                bullets.append(f"Listing rating: {rating:.1f}/5.")

    if price is not None and min_price is not None and max_price is not None:
        if min_price <= price <= max_price:
            if language == "ru":
                bullets.append(f"Цена ${float(price):.2f} находится в вашем диапазоне ${float(min_price):.2f}-${float(max_price):.2f}.")
            else:
                bullets.append(f"Price ${float(price):.2f} is inside your requested range ${float(min_price):.2f}-${float(max_price):.2f}.")
        elif price < min_price:
            if language == "ru":
                bullets.append(f"Цена ${float(price):.2f} ниже вашего диапазона ${float(min_price):.2f}-${float(max_price):.2f}.")
            else:
                bullets.append(f"Price ${float(price):.2f} is below your requested range ${float(min_price):.2f}-${float(max_price):.2f}.")
        else:
            if language == "ru":
                bullets.append(f"Цена ${float(price):.2f} выше вашего диапазона ${float(min_price):.2f}-${float(max_price):.2f}.")
            else:
                bullets.append(f"Price ${float(price):.2f} is above your requested range ${float(min_price):.2f}-${float(max_price):.2f}.")
    elif budget is not None and price is not None and price <= budget:
        delta = float(budget) - float(price)
        if language == "ru":
            if delta >= max(5.0, float(budget) * 0.1):
                bullets.append(f"Это плюс по цене: товар ниже бюджета на ${delta:.2f}.")
            else:
                bullets.append(f"Укладывается в ваш бюджет (${float(budget):.2f}).")
        else:
            if delta >= max(5.0, float(budget) * 0.1):
                bullets.append(f"Price advantage: this is ${delta:.2f} below your budget.")
            else:
                bullets.append(f"Within your budget (${float(budget):.2f}).")
    elif budget is not None and price is not None and price > budget:
        if language == "ru":
            bullets.append(f"Выше бюджета на ${float(price - budget):.2f}.")
        else:
            bullets.append(f"Over budget by ${float(price - budget):.2f}.")

    if target_price is not None and price is not None:
        diff = abs(float(price) - float(target_price))
        if language == "ru":
            bullets.append(f"Отклонение от целевой цены (${float(target_price):.2f}) составляет ${diff:.2f}.")
        else:
            bullets.append(f"Price is ${diff:.2f} away from your target (${float(target_price):.2f}).")

    if price is None:
        if language == "ru":
            bullets.append("Цена: нет данных в каталоге.")
        else:
            bullets.append("Price: Unknown from catalog.")
    return bullets


def _tco_block(product: dict) -> dict[str, Any]:
    price = product.get("price")
    lifespan = product.get("lifespan_years")
    if lifespan is not None and lifespan > 0 and price is not None:
        yearly = compute_tco(price, lifespan)
        return {
            "available": True,
            "yearly_cost": round(yearly, 2),
            "formula": "price / lifespan_years",
            "notes": f"Catalog: price={price}, lifespan_years={lifespan}.",
        }
    return {
        "available": False,
        "yearly_cost": None,
        "formula": "null",
        "notes": "Unknown from catalog (lifespan_years missing or zero).",
    }


def _unknowns(product: dict) -> list[str]:
    missing = []
    if product.get("quality_score") is None:
        missing.append("quality_score")
    if product.get("lifespan_years") is None:
        missing.append("lifespan_years")
    for f in _COMMON_MISSING_FIELDS:
        if product.get(f) is None:
            missing.append(f)
    return missing[:5]


def _safe_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text:
        return None
    m = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _price_fit_score(product: dict, budget: float | None) -> float:
    price = _safe_price(product.get("price"))
    if budget is None or budget <= 0:
        if price is None:
            return 0.1
        return max(0.0, min(0.4, 0.4 / (1.0 + (price / 400.0))))
    if price is None:
        return 0.1
    if price <= budget:
        ratio = (budget - price) / max(budget, 1.0)
        return max(0.6, 1.0 - (ratio * 0.4))
    over_ratio = (price - budget) / max(budget, 1.0)
    return max(0.0, 0.5 - over_ratio)


def _price_distance_score(product: dict, target_price: float | None) -> float:
    if target_price is None or target_price <= 0:
        return 0.0
    price = _safe_price(product.get("price"))
    if price is None:
        return 0.0
    diff_ratio = abs(price - target_price) / max(target_price, 1.0)
    return 1.0 / (1.0 + (diff_ratio * 3.0))


def _query_keywords(parsed: dict[str, Any]) -> list[str]:
    parts = []
    parts.extend(parsed.get("must_haves") or [])
    parts.extend(parsed.get("nice_to_haves") or [])
    req_kw = (parsed.get("required_keyword") or "").strip()
    if req_kw:
        parts.append(req_kw)
    unique = []
    seen = set()
    for w in parts:
        ww = str(w or "").strip().lower()
        if not ww or ww in _STOPWORDS:
            continue
        if ww not in seen:
            seen.add(ww)
            unique.append(ww)
    return unique


def _keyword_match_count(product: dict, keywords: list[str]) -> int:
    if not keywords:
        return 0
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    cat = (product.get("category") or "").lower()
    count = 0
    for kw in keywords:
        if kw in title:
            count += 2
        elif kw in desc or kw in cat:
            count += 1
    return count


def _has_keyword(product: dict, keyword: str) -> bool:
    t = ((product.get("title") or "") + " " + (product.get("description") or "")).lower()
    return keyword.lower() in t


def _matches_any_keyword(product: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    t = ((product.get("title") or "") + " " + (product.get("description") or "")).lower()
    return any(kw.lower() in t for kw in keywords)


def recommend_from_catalog(
    user_text: str,
    products: list[dict],
    k: int = 3,
) -> dict[str, Any]:
    """Deterministic catalog-only recommender. Keyword matches are never demoted out of top-k by budget."""
    parsed = _parse_request(user_text)
    budget = parsed.get("budget")
    min_price = parsed.get("min_price")
    max_price = parsed.get("max_price")
    target_price = parsed.get("target_price")
    category = parsed.get("category")
    language = "ru" if _contains_cyrillic(user_text) else "en"
    requested_family = _detect_requested_product_family(user_text)
    requested_family_label = _product_family_label(requested_family, language)
    query_keywords = _query_keywords(parsed)
    query_concepts = _detect_query_concepts(user_text)
    strict_keywords = [k for k in query_keywords if k not in _NON_DISCRIMINATIVE_KEYWORDS]
    active_keywords = strict_keywords or query_keywords
    weights = _keyword_weights(active_keywords, products)
    buyer_focus = _buyer_focus_summary(parsed, active_keywords, query_concepts, requested_family, language)

    candidates = _dedupe_products(list(products))
    if requested_family:
        family_matches = [p for p in candidates if _product_family_score(p, requested_family) > 0]
        if family_matches:
            candidates = family_matches

    if active_keywords:
        discriminative_terms = [kw for kw, w in weights.items() if w >= 1.45]
        if discriminative_terms:
            strict_keyword_candidates = [
                p
                for p in candidates
                if any(term in f"{(p.get('title') or '').lower()} {(p.get('description') or '').lower()} {(p.get('category') or '').lower()}" for term in discriminative_terms)
            ]
            if strict_keyword_candidates:
                candidates = strict_keyword_candidates
        else:
            strict_keyword_candidates = [p for p in candidates if _keyword_match_score(p, weights) > 0]
            if strict_keyword_candidates:
                candidates = strict_keyword_candidates

    if min_price is not None and max_price is not None:
        in_range = [p for p in candidates if (_safe_price(p.get("price")) is not None and min_price <= _safe_price(p.get("price")) <= max_price)]
        if in_range:
            candidates = in_range
    elif max_price is not None:
        under_cap = [p for p in candidates if (_safe_price(p.get("price")) is not None and _safe_price(p.get("price")) <= max_price)]
        if under_cap:
            candidates = under_cap
    elif min_price is not None:
        above_floor = [p for p in candidates if (_safe_price(p.get("price")) is not None and _safe_price(p.get("price")) >= min_price)]
        if above_floor:
            candidates = above_floor
    elif budget is not None:
        under_budget = [p for p in candidates if (_safe_price(p.get("price")) is not None and _safe_price(p.get("price")) <= budget)]
        if under_budget:
            candidates = under_budget

    metrics_cache: dict[int, dict[str, Any]] = {}

    def candidate_metrics(p: dict) -> dict[str, Any]:
        key = id(p)
        cached = metrics_cache.get(key)
        if cached is not None:
            return cached

        price = _safe_price(p.get("price"))
        matched_terms = _matched_query_terms(p, active_keywords)
        matched_count = len(matched_terms)
        keyword_coverage = (float(matched_count) / float(len(active_keywords))) if active_keywords else 0.0
        kw_score = _keyword_match_score(p, weights)
        concept_matches = _matched_concepts(p, query_concepts)
        concept_match_count = len(concept_matches)
        concept_score = _concept_match_score(p, query_concepts)
        family_score = _product_family_score(p, requested_family)
        family_bucket = 0 if (not requested_family or family_score > 0) else 1
        concept_bucket = 0 if ((not query_concepts) or concept_match_count > 0) else 1
        keyword_bucket = 0 if ((not active_keywords) or matched_count > 0) else 1
        category_bucket = 0 if (not category or (p.get("category") or "").lower() == category.lower()) else 1

        if price is None:
            range_bucket = 2
        elif min_price is not None and max_price is not None:
            if min_price <= price <= max_price:
                range_bucket = 0
            elif price < min_price:
                range_bucket = 1
            else:
                range_bucket = 2
        elif min_price is not None:
            range_bucket = 0 if price >= min_price else 1
        elif max_price is not None:
            range_bucket = 0 if price <= max_price else 2
        else:
            range_bucket = 0

        if budget is None:
            budget_bucket = 0
        elif price is None:
            budget_bucket = 2
        elif price <= budget:
            budget_bucket = 0
        else:
            budget_bucket = 3

        target_missing_bucket = 0 if (target_price is None or price is not None) else 1
        rel = _relevance_score(p, parsed)
        price_fit = _price_fit_score(p, budget)
        distance_fit = _price_distance_score(p, target_price)
        val, neg_tco = _value_rank_term(p)

        evidence_score, evidence_signals, marketing_only = _evidence_score(p, query_concepts, active_keywords)
        trust_score = _trust_signal(p)
        rating = _safe_rating(p)
        reviews_count = _safe_reviews_count(p)
        spec_count = _spec_count(p)
        marketing_bucket = 1 if marketing_only else 0

        out = {
            "price": price,
            "matched_count": matched_count,
            "keyword_coverage": keyword_coverage,
            "kw_score": kw_score,
            "concept_matches": concept_matches,
            "concept_match_count": concept_match_count,
            "concept_score": concept_score,
            "family_score": family_score,
            "family_bucket": family_bucket,
            "concept_bucket": concept_bucket,
            "keyword_bucket": keyword_bucket,
            "category_bucket": category_bucket,
            "range_bucket": range_bucket,
            "budget_bucket": budget_bucket,
            "target_missing_bucket": target_missing_bucket,
            "rel": rel,
            "price_fit": price_fit,
            "distance_fit": distance_fit,
            "value": val,
            "neg_tco": neg_tco,
            "evidence_score": evidence_score,
            "evidence_signals": evidence_signals,
            "marketing_only": marketing_only,
            "marketing_bucket": marketing_bucket,
            "trust_score": trust_score,
            "rating": rating,
            "reviews_count": reviews_count,
            "spec_count": spec_count,
        }
        metrics_cache[key] = out
        return out

    def sort_key(p: dict):
        m = candidate_metrics(p)
        return (
            m["family_bucket"],
            m["concept_bucket"],
            m["range_bucket"],
            m["budget_bucket"],
            m["category_bucket"],
            m["target_missing_bucket"],
            m["marketing_bucket"],
            -m["family_score"],
            -m["concept_match_count"],
            -m["evidence_score"],
            -m["trust_score"],
            -m["concept_score"],
            -m["distance_fit"],
            -m["rel"],
            -m["price_fit"],
            -m["kw_score"],
            -m["value"],
            m["neg_tco"],
            str(p.get("id") or ""),
        )

    candidates.sort(key=sort_key)
    concept_match_counts = [candidate_metrics(p)["concept_match_count"] for p in candidates]
    max_concept_match_count = max(concept_match_counts) if concept_match_counts else 0

    recommendations = []
    seen_rec_keys: set[str] = set()
    for p in candidates:
        if len(recommendations) >= k:
            break
        m = candidate_metrics(p)
        rid = str(p.get("id", "")).strip()
        rtitle = _normalized_title(p.get("title"))
        rec_key = rid or f"title:{rtitle}"
        if rec_key in seen_rec_keys:
            continue
        seen_rec_keys.add(rec_key)

        numeric_price = m["price"]
        matched_concepts = m["concept_matches"]
        rank = len(recommendations) + 1
        rec = {
            "id": rid,
            "title": str(p.get("title", "")),
            "price": numeric_price,
            "category": str(p.get("category", "")),
            "score_explanation": _score_explanation_bullets(
                p,
                budget,
                query_terms=active_keywords,
                target_price=target_price,
                min_price=min_price,
                max_price=max_price,
                matched_concepts=matched_concepts,
                total_query_concepts=len(query_concepts),
                max_concept_match_count=max_concept_match_count,
                buyer_focus_summary=buyer_focus,
                evidence_signals=m["evidence_signals"],
                spec_count=m["spec_count"],
                rating=m["rating"],
                reviews_count=m["reviews_count"],
                marketing_only=m["marketing_only"],
                requested_family_label=requested_family_label,
                family_match_score=m["family_score"],
                language=language,
                rank=rank,
                total_candidates=len(candidates),
                is_top_pick=(rank == 1),
            ),
            "tco": _tco_block(p),
            "unknowns": _unknowns(p),
        }
        recommendations.append(rec)
    follow_up = None
    if budget is not None and recommendations and all((r.get("price") or 0) > budget for r in recommendations):
        follow_up = f"Want options slightly above ${budget:.0f}?"
    return {
        "parsed_request": {
            "budget": parsed.get("budget"),
            "category": parsed.get("category"),
            "must_haves": parsed.get("must_haves", []),
            "nice_to_haves": parsed.get("nice_to_haves", []),
        },
        "recommendations": recommendations,
        "follow_up_question": follow_up,
    }
