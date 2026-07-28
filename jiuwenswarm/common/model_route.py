"""Pure model-route selection and trusted actual-route receipt helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Iterable

from jiuwenswarm.common.e2a.constants import (
    E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY,
    E2A_INTERNAL_EXPECTED_MODEL_ROUTE_KEY,
)


@dataclass(frozen=True)
class ModelRouteCandidate:
    entry_index: int
    canonical_model_key: str
    model_name: str
    provider: str
    alias: str
    is_default: bool


@dataclass(frozen=True)
class ModelRouteIndex:
    candidates: tuple[ModelRouteCandidate, ...]
    routes_by_key: dict[str, ModelRouteCandidate]
    default_key: str

    def resolve(self, requested_model: str = "") -> ModelRouteCandidate | None:
        requested = str(requested_model or "").strip()
        return self.routes_by_key.get(
            requested or self.default_key,
            self.routes_by_key.get(self.default_key),
        )


@dataclass(frozen=True)
class ActualModelRouteReceipt:
    canonical_model_key: str
    provider: str
    source_request_id: str
    mode: str

    def to_dict(self) -> dict[str, str]:
        return {
            "canonical_model_key": self.canonical_model_key,
            "provider": self.provider,
            "source_request_id": self.source_request_id,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class ExpectedModelRoute:
    canonical_model_key: str
    provider: str
    mode: str

    def to_dict(self) -> dict[str, str]:
        return {
            "canonical_model_key": self.canonical_model_key,
            "provider": self.provider,
            "mode": self.mode,
        }


def enumerate_model_route_candidates(
    entries: Iterable[object],
) -> tuple[ModelRouteCandidate, ...]:
    """Return stable canonical candidates without constructing model clients."""

    candidates: list[ModelRouteCandidate] = []
    name_counts: dict[str, int] = {}
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        config = entry.get("model_client_config")
        if not isinstance(config, dict):
            continue
        model_name = str(config.get("model_name") or "").strip()
        if not model_name:
            continue
        index = name_counts.get(model_name, 0)
        name_counts[model_name] = index + 1
        candidates.append(
            ModelRouteCandidate(
                entry_index=entry_index,
                canonical_model_key=f"{model_name}#{index}",
                model_name=model_name,
                provider=str(config.get("client_provider") or "OpenAI").strip(),
                alias=str(entry.get("alias") or "").strip(),
                is_default=entry.get("is_default") is True,
            )
        )
    return tuple(candidates)


def build_model_route_index(
    entries: Iterable[object],
    *,
    available_canonical_keys: Collection[str] | None = None,
) -> ModelRouteIndex:
    """Build selectable keys using the same order as DeepAdapter's model cache.

    ``available_canonical_keys`` lets the runtime exclude candidates whose
    model construction failed while preserving their original ``#index``.
    """

    candidates = enumerate_model_route_candidates(entries)
    available = (
        None
        if available_canonical_keys is None
        else frozenset(available_canonical_keys)
    )
    routes_by_key: dict[str, ModelRouteCandidate] = {}
    ordered_model_names: list[str] = []
    first_canonical_key = ""
    for candidate in candidates:
        if (
            available is not None
            and candidate.canonical_model_key not in available
        ):
            continue
        routes_by_key[candidate.canonical_model_key] = candidate
        if not first_canonical_key:
            first_canonical_key = candidate.canonical_model_key
        if candidate.model_name not in ordered_model_names:
            ordered_model_names.append(candidate.model_name)
        if candidate.is_default:
            routes_by_key[candidate.model_name] = candidate
        if (
            candidate.alias
            and candidate.alias != candidate.model_name
            and candidate.alias not in routes_by_key
        ):
            routes_by_key[candidate.alias] = candidate

    default_key = next(
        (name for name in ordered_model_names if name in routes_by_key),
        first_canonical_key,
    )
    return ModelRouteIndex(
        candidates=candidates,
        routes_by_key=routes_by_key,
        default_key=default_key,
    )


def actual_model_route_metadata(
    receipt: ActualModelRouteReceipt,
) -> dict[str, dict[str, str]]:
    return {E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY: receipt.to_dict()}


def parse_actual_model_route_receipt(
    metadata: Any,
    *,
    expected_source_request_id: str | None = None,
) -> ActualModelRouteReceipt | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    fields = {
        "canonical_model_key": str(raw.get("canonical_model_key") or "").strip(),
        "provider": str(raw.get("provider") or "").strip(),
        "source_request_id": str(raw.get("source_request_id") or "").strip(),
        "mode": str(raw.get("mode") or "").strip(),
    }
    if not all(fields.values()):
        return None
    expected = str(expected_source_request_id or "").strip()
    if expected and fields["source_request_id"] != expected:
        return None
    return ActualModelRouteReceipt(**fields)


def parse_expected_model_route(metadata: Any) -> ExpectedModelRoute | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(E2A_INTERNAL_EXPECTED_MODEL_ROUTE_KEY)
    if not isinstance(raw, dict):
        return None
    fields = {
        key: str(raw.get(key) or "").strip()
        for key in ("canonical_model_key", "provider", "mode")
    }
    if not all(fields.values()):
        return None
    return ExpectedModelRoute(**fields)
