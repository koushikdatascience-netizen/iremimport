"""Suggestion scoring for excise-to-Madhushala item mapping."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.services.normalizer import normalize_brand, parse_int


STOP_WORDS = {
    "aged",
    "single",
    "highland",
    "malt",
    "scotch",
    "whisky",
    "whiskey",
    "grain",
    "rare",
    "blue",
    "years",
    "year",
    "glass",
    "bottle",
    "ml",
}


def normalize_match_text(value: str) -> str:
    text = normalize_brand(value)
    text = re.sub(r"\b(\d+)\s*years?\b", r"\1y", text)
    text = re.sub(r"\b(\d+)\s*ml\b", r"\1", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_match_text(value).split()
        if token and token not in STOP_WORDS and not token.isdigit()
    }


def _trigrams(value: str) -> set[str]:
    compact = normalize_match_text(value).replace(" ", "")
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


class MatchIndex:
    """Pre-index a Madhushala catalogue so every source row does not scan it all."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = [item for item in items if isinstance(item, dict)]
        self.by_ml: dict[int, list[int]] = {}
        self.by_token: dict[str, list[int]] = {}
        self.by_trigram: dict[str, list[int]] = {}

        for index, item in enumerate(self.items):
            ml = ml_value(item)
            if ml:
                self.by_ml.setdefault(ml, []).append(index)

            name = str(item.get("itemName") or "")
            for token in tokens(name):
                self.by_token.setdefault(token, []).append(index)
            for trigram in _trigrams(name):
                self.by_trigram.setdefault(trigram, []).append(index)

    def candidates(self, excise_item: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(excise_item.get("itemName") or excise_item.get("brand") or "")
        source_ml = ml_value(
            {
                "measureMl": excise_item.get("measureMl"),
                "ml": excise_item.get("ml"),
                "itemName": name,
            }
        )
        indexes: set[int] = set()

        if source_ml:
            indexes.update(self.by_ml.get(source_ml, []))

        for token in tokens(name):
            indexes.update(self.by_token.get(token, []))

        source_trigrams = _trigrams(name)
        trigram_hits: dict[int, int] = {}
        for trigram in source_trigrams:
            for index in self.by_trigram.get(trigram, []):
                trigram_hits[index] = trigram_hits.get(index, 0) + 1

        if source_trigrams:
            threshold = max(2, len(source_trigrams) // 5)
            indexes.update(
                index
                for index, count in trigram_hits.items()
                if count >= threshold
            )

        # Preserve correctness for unusual/noisy source names: if the index
        # cannot find any credible candidates, fall back to the full catalogue.
        if not indexes:
            return self.items

        return [self.items[index] for index in sorted(indexes)]


def ml_value(item: dict[str, Any]) -> int:
    explicit = item.get("ml") or item.get("measureMl")
    if explicit:
        return parse_int(explicit)
    return extract_ml_from_text(str(item.get("itemName") or ""))


def extract_ml_from_text(value: str) -> int:
    match = re.search(r"\b(\d{2,4})\s*ml\b", value, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return parse_int(value)


def score_item(excise_item: dict[str, Any], madhushala_item: dict[str, Any]) -> dict[str, Any]:
    excise_name = excise_item.get("itemName") or excise_item.get("brand") or ""
    madhushala_name = madhushala_item.get("itemName") or ""
    excise_ml = ml_value({"measureMl": excise_item.get("measureMl"), "ml": excise_item.get("ml"), "itemName": excise_name})
    madhushala_ml = ml_value(madhushala_item)

    excise_text = normalize_match_text(excise_name)
    madhushala_text = normalize_match_text(madhushala_name)
    fuzzy = SequenceMatcher(None, excise_text, madhushala_text).ratio()

    excise_tokens = tokens(excise_name)
    madhushala_tokens = tokens(madhushala_name)
    overlap = len(excise_tokens & madhushala_tokens) / max(len(excise_tokens), 1)
    has_name_signal = overlap > 0 or fuzzy >= 0.55

    score = 0.0
    reasons: list[str] = []

    if not has_name_signal:
        return {
            "score": 0,
            "item": madhushala_item,
            "reasons": [],
        }

    if excise_ml and madhushala_ml and excise_ml == madhushala_ml:
        score += 45
        reasons.append(f"ML {excise_ml} matches")
    elif excise_ml and madhushala_ml:
        score -= 30

    bottles_per_case = parse_int(excise_item.get("bottlesPerCase") or 0)
    packing = parse_int(madhushala_item.get("packing") or 0)
    if bottles_per_case and packing and bottles_per_case == packing:
        score += 10
        reasons.append(f"Packing {packing} matches")

    score += fuzzy * 30
    score += overlap * 15

    if overlap:
        reasons.append("Name words overlap")
    if fuzzy >= 0.45:
        reasons.append("Name is similar")

    return {
        "score": round(max(score, 0), 2),
        "item": madhushala_item,
        "reasons": reasons,
    }


def suggest_matches(
    excise_item: dict[str, Any],
    madhushala_items: list[dict[str, Any]],
    *,
    limit: int = 8,
    index: MatchIndex | None = None,
) -> list[dict[str, Any]]:
    candidates = index.candidates(excise_item) if index is not None else madhushala_items
    scored = [score_item(excise_item, item) for item in candidates]
    useful = [entry for entry in scored if entry["score"] > 0]
    return sorted(useful, key=lambda entry: entry["score"], reverse=True)[:limit]


def item_initials(item_name: str) -> str:
    return "".join(token[0] for token in normalize_match_text(item_name).split() if token)


def score_dropdown_search(item: dict[str, Any], query: str) -> float:
    clean_query = normalize_match_text(query)
    if not clean_query:
        return 0

    item_name = normalize_match_text(str(item.get("itemName") or ""))
    compact_name = item_name.replace(" ", "")
    compact_query = clean_query.replace(" ", "")
    code = normalize_match_text(str(item.get("itemCode") or ""))
    short_code = normalize_match_text(str(item.get("shortCode") or ""))
    barcode = normalize_match_text(str(item.get("barcode") or ""))
    barcode2 = normalize_match_text(str(item.get("barcode2") or ""))
    barcode3 = normalize_match_text(str(item.get("barcode3") or ""))
    initials = item_initials(str(item.get("itemName") or ""))
    query_tokens = clean_query.split()
    words = item_name.split()
    is_short_query = len(compact_query) <= 1
    all_tokens_match = all(
        any(
            word.startswith(token) or (len(token) >= 3 and token in word)
            for word in words
        )
        for token in query_tokens
    )

    score = 0.0
    direct_match = False
    if code == clean_query:
        score += 120
        direct_match = True
    if code.startswith(clean_query):
        score += 80
        direct_match = True
    if short_code and short_code.startswith(clean_query):
        score += 75
        direct_match = True
    if barcode and barcode.startswith(clean_query):
        score += 70
        direct_match = True
    if barcode2 and barcode2.startswith(clean_query):
        score += 65
        direct_match = True
    if barcode3 and barcode3.startswith(clean_query):
        score += 65
        direct_match = True
    if item_name.startswith(clean_query):
        score += 100
        direct_match = True
    if compact_name.startswith(compact_query):
        score += 85
        direct_match = True
    if initials.startswith(compact_query):
        score += 90
        direct_match = True
    if not is_short_query and len(clean_query) >= 3 and clean_query in item_name:
        score += 45
        direct_match = True

    if not direct_match and not all_tokens_match:
        return 0

    for token in query_tokens:
        if any(word.startswith(token) for word in words):
            score += 25
        elif len(token) >= 3 and any(token in word for word in words):
            score += 12

    if str(item.get("ml") or "") == clean_query:
        score += 10
    return score
