"""Learning and context rules. Outcome sign is not a quality grade."""

from __future__ import annotations


def classify_decision(process_assessment: str, outcome_sign: str) -> str:
    return process_assessment


def select_context(policy: dict, lessons: list[dict]) -> dict:
    cap = int(policy["max_general_lessons"])
    ranked = sorted(lessons, key=lambda item: item.get("relevance", 0), reverse=True)[:cap]
    return {
        "always_include": list(policy["always_include"]),
        "lessons": ranked,
    }
