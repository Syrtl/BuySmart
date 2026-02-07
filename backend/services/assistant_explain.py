"""LLM-backed explanation service for deterministic ranked candidates."""

from __future__ import annotations

import json
import os
from typing import Any


def _safe_num(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_candidates(raw_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in raw_candidates[:8]:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not item_id and not title:
            continue
        out.append(
            {
                "id": item_id,
                "title": title or item_id,
                "qualityScore": round(_safe_num(row.get("qualityScore")), 2),
                "priceFitScore": round(_safe_num(row.get("priceFitScore")), 2),
                "requirementMatch": round(_safe_num(row.get("requirementMatch")), 2),
                "materialScore": round(_safe_num(row.get("materialScore")), 2),
                "totalScore": round(_safe_num(row.get("totalScore")), 2),
                "flags": [str(x) for x in (row.get("flags") or [])][:6],
                "price": row.get("price"),
            }
        )
    return out


def _fallback_explanation(
    *,
    user_text: str,
    intent: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    selected_id: str | None,
) -> dict[str, Any]:
    selected = None
    if selected_id:
        for item in candidates:
            if str(item.get("id") or "") == selected_id:
                selected = item
                break
    if selected is None and candidates:
        selected = candidates[0]

    if selected is None:
        return {
            "summary": "No candidates available to explain.",
            "selectedId": selected_id or "",
            "items": [],
        }

    over_budget = "over_budget" in (selected.get("flags") or [])
    price_note = "above budget" if over_budget else "within or near budget"
    summary = (
        f"{selected.get('title')} ranks highest because it balances quality "
        f"({selected.get('qualityScore')}/100), price fit ({selected.get('priceFitScore')}/100), "
        f"and requirement coverage ({selected.get('requirementMatch')}/100)."
    )

    explanation = (
        f"Professional recommendation: this option has a quality score of {selected.get('qualityScore')}/100, "
        f"price fit of {selected.get('priceFitScore')}/100 ({price_note}), and requirement match of "
        f"{selected.get('requirementMatch')}/100. Material score is {selected.get('materialScore')}/100."
    )

    return {
        "summary": summary,
        "selectedId": str(selected.get("id") or ""),
        "items": [
            {
                "id": str(selected.get("id") or ""),
                "title": str(selected.get("title") or ""),
                "explanation": explanation,
            }
        ],
    }


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def explain_candidates(
    *,
    user_text: str,
    intent: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    selected_id: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_candidates(candidates)
    if not normalized:
        return {
            "summary": "No candidates available to explain.",
            "selectedId": selected_id or "",
            "items": [],
        }

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return _fallback_explanation(
            user_text=user_text,
            intent=intent,
            candidates=normalized,
            selected_id=selected_id,
        )

    try:
        import openai
    except Exception:
        return _fallback_explanation(
            user_text=user_text,
            intent=intent,
            candidates=normalized,
            selected_id=selected_id,
        )

    system_prompt = (
        "You are a professional procurement consultant. "
        "Explain ONLY the provided candidates. NEVER invent items or metrics. "
        "Every explanation must explicitly reference qualityScore, priceFitScore, and requirementMatch. "
        "Be concise and factual. Return JSON only."
    )

    payload = {
        "userText": user_text,
        "intent": intent or {},
        "selectedId": selected_id,
        "candidates": normalized,
    }

    user_prompt = (
        "Return strict JSON with this schema:\n"
        "{\n"
        "  \"summary\": string,\n"
        "  \"selectedId\": string,\n"
        "  \"items\": [\n"
        "    {\"id\": string, \"title\": string, \"explanation\": string}\n"
        "  ]\n"
        "}\n"
        "Use only IDs from candidates.\n"
        f"Data:\n{json.dumps(payload, ensure_ascii=True)}"
    )

    try:
        client = openai.OpenAI(api_key=api_key.strip())
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        content = (response.choices[0].message.content or "").strip()
        parsed = _extract_json(content)
        if not isinstance(parsed, dict):
            raise ValueError("Invalid LLM JSON")

        items = parsed.get("items")
        if not isinstance(items, list):
            raise ValueError("Invalid items list")

        allowed_ids = {str(row.get("id") or "") for row in normalized}
        cleaned_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or "").strip()
            if iid not in allowed_ids:
                continue
            cleaned_items.append(
                {
                    "id": iid,
                    "title": str(item.get("title") or "").strip() or iid,
                    "explanation": str(item.get("explanation") or "").strip(),
                }
            )

        if not cleaned_items:
            raise ValueError("No valid explained items")

        selected = str(parsed.get("selectedId") or "").strip()
        if selected not in allowed_ids:
            selected = str(cleaned_items[0]["id"])

        return {
            "summary": str(parsed.get("summary") or "").strip() or "Candidate explanation generated.",
            "selectedId": selected,
            "items": cleaned_items,
        }
    except Exception:
        return _fallback_explanation(
            user_text=user_text,
            intent=intent,
            candidates=normalized,
            selected_id=selected_id,
        )
