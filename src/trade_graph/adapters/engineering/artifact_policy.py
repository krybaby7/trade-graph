"""Owner-pinned R1 data grammars. No imports, SQL, executable plugins or template eval."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

PREFIXES = ("artifacts/", "prompts/", "strategies/templates/")
PROMPTS = frozenset({"engineer", "leader", "learning", "researcher", "trader"})
STRATEGIES = {"range_reversion": "range-reversion", "slow_trend_pullback": "slow-trend-pullback"}
ROLES = frozenset({"leader", "secretary", "trader", "learning", "research", "optimisation", "engineer"})
REQUIRED = ("mandate_obligations", "active_safety")
FORBIDDEN = ("subprocess", "os.system", "drop table", "169.254.169.254", "openai_api_key",
             "anthropic_api_key", "begin private key", "__import__", "eval(", "exec(")


def artifact_class(path: str) -> str:
    p = PurePosixPath(path)
    if p.as_posix() != path or p.is_absolute() or any(x.startswith(".") for x in p.parts) or "\\" in path:
        raise ValueError("noncanonical or protected artifact path")
    if path == "artifacts/context_policy.json":
        return "context_policy"
    if path == "artifacts/report_layout.json":
        return "report_template"
    if path == "artifacts/schedules.json":
        return "schedule"
    if path == "artifacts/model_routing.json":
        return "approved_model_routing"
    if path.startswith("prompts/") and p.parent.as_posix() == "prompts" and p.stem in PROMPTS and p.suffix == ".md":
        return "prompt"
    if p.parent.as_posix() == "strategies/templates" and p.stem in STRATEGIES and p.suffix == ".json":
        return "artifact_config"
    raise ValueError("unregistered R1 artifact path/class")


def _object(text: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def bad_constant(_):
        raise ValueError("nonfinite JSON number")

    result = json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)
    if not isinstance(result, dict):
        raise ValueError("artifact must be a JSON object")
    return result


def validate(files: dict[str, str]) -> list[str]:
    failures = []
    if "artifacts/context_policy.json" not in files:
        failures.append("missing context policy")
    for path, text in files.items():
        try:
            kind = artifact_class(path)
            if any(marker in text.casefold() for marker in FORBIDDEN):
                raise ValueError("forbidden content")
            if kind == "prompt":
                if not text.strip() or len(text.encode()) > 16384:
                    raise ValueError("prompt size bound")
                continue
            doc = _object(text)
            if kind == "context_policy":
                if set(doc) != {"schema_version", "max_general_lessons", "always_include"}:
                    raise ValueError("context policy fields")
                n = doc["max_general_lessons"]
                if type(n) is not int or not 1 <= n <= 8:
                    raise ValueError("lesson cap out of range")
                if doc["schema_version"] != 1 or type(doc["schema_version"]) is not int:
                    raise ValueError("context policy schema")
                if not isinstance(doc["always_include"], list) or sorted(doc["always_include"]) != sorted(REQUIRED):
                    raise ValueError("required lessons missing or unknown obligations")
            elif kind == "report_template":
                fields = {"summary", "financial", "costs", "engineering", "risks"}
                sections = doc.get("sections")
                if (set(doc) != {"schema_version", "sections"} or doc["schema_version"] != 1
                        or not isinstance(sections, list) or not 1 <= len(sections) <= 5
                        or any(type(x) is not str or x not in fields for x in sections)
                        or len(set(sections)) != len(sections)):
                    raise ValueError("report layout grammar")
            elif kind == "schedule":
                intervals = doc.get("interval_seconds")
                if (set(doc) != {"schema_version", "interval_seconds"} or doc["schema_version"] != 1
                        or not isinstance(intervals, dict) or not intervals
                        or not set(intervals).issubset(ROLES)
                        or any(type(x) is not int or not 10 <= x <= 86400 for x in intervals.values())):
                    raise ValueError("schedule grammar; protection controls are not artifacts")
            elif kind == "approved_model_routing":
                routes = doc.get("routes")
                model_roles = ROLES - {"secretary"}
                if (set(doc) != {"schema_version", "routes"} or type(doc["schema_version"]) is not int
                        or doc["schema_version"] != 1 or not isinstance(routes, dict) or not routes
                        or not set(routes).issubset(model_roles)
                        or any(type(x) is not str or not 1 <= len(x) <= 128
                               or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
                                      for c in x) for x in routes.values())):
                    raise ValueError("model routing grammar; only protected price-card references are permitted")
            else:
                keys = {"strategy_id", "status", "features", "entry", "exit", "sizing_envelope", "invalidation",
                        "execution", "costs", "validation"}
                features = {"range_high", "range_low", "midpoint", "sma_20", "sma_50", "pullback_from_high"}
                if (set(doc) != keys or doc["strategy_id"] != STRATEGIES[PurePosixPath(path).stem]
                        or doc["status"] != "unproven" or not isinstance(doc["features"], list)
                        or not doc["features"] or len(doc["features"]) > 6
                        or any(type(x) is not str or x not in features for x in doc["features"])
                        or any(type(doc[k]) is not str or not 1 <= len(doc[k]) <= 4000 for k in keys - {"features"})):
                    raise ValueError("registered non-executable strategy grammar")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            failures.append(f"{path}: {str(exc)[:200]}")
    return failures[:32]
