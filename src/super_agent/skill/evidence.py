"""Associate claims with host-observed evidence without deciding semantic truth.

No retrieval, execution, persistence or acceptance happens here. The trusted host
reads original bytes, supplies current Evidence records and checks whether their
contents actually support the claims. Model-written records cannot attest to
themselves. TaskCheck remains the authority for delivery.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from heapq import heappop, heappush
import json

from super_agent.core import require_text
from super_agent.skill.content import content_digest


@dataclass(frozen=True)
class Evidence:
    """Current host observation of one stable source or experiment reference.

    sha256 identifies the exact source body or experiment report, not its URL.
    The host chooses freshness policy. expires_at is exclusive; no expiry means
    no time-based invalidation, not a promise of permanent correctness.
    """

    reference: str
    sha256: str
    kind: str
    observed_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", require_text(self.reference, "evidence reference"))
        _digest(self.sha256)
        if self.kind not in {"source", "experiment"}:
            raise ValueError("evidence kind must be source or experiment")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at))
            if self.expires_at <= self.observed_at:
                raise ValueError("evidence expiry must follow its observation")


@dataclass(frozen=True)
class EvidenceLink:
    """A claim's pin to exact bytes and a passage/result location within them.

    Locator presence is checked; its existence and meaning need host review.
    Repeated links to one reference never establish independent confirmation.
    """

    reference: str
    sha256: str
    locator: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", require_text(self.reference, "evidence reference"))
        _digest(self.sha256)
        object.__setattr__(self, "locator", require_text(self.locator, "evidence location"))


@dataclass(frozen=True)
class Claim:
    """A scoped observation, inference or hypothesis, with retained opposition.

    depends_on refers to other Claim IDs, not evidence. Observations require
    direct support; inferences can depend on linked claims. Hypotheses remain
    hypotheses even when all their links are current.
    """

    claim_id: str
    statement: str
    scope: str
    kind: str
    supports: tuple[EvidenceLink, ...] = ()
    counterevidence: tuple[EvidenceLink, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("claim_id", "statement", "scope"):
            object.__setattr__(self, name, require_text(getattr(self, name), name))
        if self.kind not in {"observation", "inference", "hypothesis"}:
            raise ValueError("claim kind must be observation, inference or hypothesis")
        for name in ("supports", "counterevidence"):
            if isinstance(getattr(self, name), (str, bytes)):
                raise TypeError("claim links must be an array of EvidenceLink objects")
            links = tuple(getattr(self, name))
            if any(not isinstance(link, EvidenceLink) for link in links):
                raise TypeError("claim links require EvidenceLink objects")
            if len(set(links)) != len(links):
                raise ValueError("duplicate evidence link")
            object.__setattr__(self, name, tuple(sorted(
                links, key=lambda link: (link.reference, link.sha256, link.locator))))
        if isinstance(self.depends_on, str):
            raise TypeError("claim dependencies must be an array of IDs")
        dependencies = tuple(require_text(item, "dependency ID") for item in self.depends_on)
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("duplicate claim dependency")
        object.__setattr__(self, "depends_on", tuple(sorted(dependencies)))


def assess_claims(
    claims: Iterable[Claim], evidence: Iterable[Evidence], *, now: datetime,
) -> dict[str, object]:
    """Report linkage and transitive review needs, plus a TaskCheck snapshot.

    Missing, changed, expired or not-yet-observed support AND counterevidence
    require review. Unresolved counterevidence is contested; no votes or scores
    resolve it. A dependent claim requires review if a prerequisite is anything
    other than linked. Invalid graphs raise rather than silently dropping edges.

    The snapshot includes claim definitions, referenced current records and their
    validity states. It excludes the ticking clock and unrelated records, so it
    changes at freshness boundaries without expiring every second. Recompute it
    in the host's TaskCheck.snapshot before review and delivery. This is a current
    view, not a background watcher or a history of intervening source changes.
    """
    instant = _utc(now)
    ordered = _ordered_claims(claims)
    catalog: dict[str, Evidence] = {}
    for item in evidence:
        if not isinstance(item, Evidence):
            raise TypeError("evidence catalog requires Evidence objects")
        if item.reference in catalog:
            raise ValueError(f"duplicate evidence reference: {item.reference}")
        catalog[item.reference] = item
    rows: dict[str, dict[str, object]] = {}
    for claim in ordered:
        rows[claim.claim_id] = _assess_claim(claim, catalog, instant, rows)
    results = [rows[key] for key in sorted(rows)]
    encoded = json.dumps(results, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    return {
        "as_of": instant.isoformat(),
        "status": "linked" if all(row["status"] == "linked" for row in results) else "needs_review",
        "claims": results,
        "snapshot": {"research.evidence": content_digest((("claims.json", encoded),))},
        "note": "linkage only; host must verify source authenticity, semantic support and experiments",
    }


def _ordered_claims(claims: Iterable[Claim]) -> tuple[Claim, ...]:
    graph: dict[str, Claim] = {}
    for claim in claims:
        if not isinstance(claim, Claim):
            raise TypeError("claims require Claim objects")
        if claim.claim_id in graph:
            raise ValueError(f"duplicate claim ID: {claim.claim_id}")
        graph[claim.claim_id] = claim
    if not graph:
        raise ValueError("at least one claim is required")
    counts = {key: len(claim.depends_on) for key, claim in graph.items()}
    dependents: dict[str, list[str]] = {key: [] for key in graph}
    for claim in graph.values():
        for dependency in claim.depends_on:
            if dependency not in graph:
                raise ValueError(f"unknown claim dependency: {dependency}")
            dependents[dependency].append(claim.claim_id)
    ready: list[str] = []
    for key, count in counts.items():
        if count == 0:
            heappush(ready, key)
    ordered = []
    while ready:
        key = heappop(ready)
        ordered.append(graph[key])
        for dependent in dependents[key]:
            counts[dependent] -= 1
            if counts[dependent] == 0:
                heappush(ready, dependent)
    if len(ordered) != len(graph):
        raise ValueError("claim dependencies contain a cycle")
    return tuple(ordered)


def _assess_claim(
    claim: Claim, catalog: dict[str, Evidence], now: datetime,
    assessed: dict[str, dict[str, object]],
) -> dict[str, object]:
    reasons = []
    relations = {}
    for relation in ("supports", "counterevidence"):
        links = [_assess_link(link, catalog, now) for link in getattr(claim, relation)]
        relations[relation] = links
        for link in links:
            reasons.extend({"code": issue, "reference": link["reference"], "relation": relation}
                           for issue in link["issues"])
    if (claim.kind == "observation" and not claim.supports or
            claim.kind == "inference" and not claim.supports and not claim.depends_on):
        reasons.append({"code": "missing_support"})
    reasons.extend({"code": "unresolved_dependency", "claim_id": dependency}
                   for dependency in claim.depends_on if assessed[dependency]["status"] != "linked")
    if reasons:
        status = "needs_review"
    elif claim.counterevidence:
        status = "contested"
    elif claim.kind == "hypothesis":
        status = "hypothesis"
    else:
        status = "linked"
    if claim.counterevidence:
        reasons.append({"code": "counterevidence_present"})
    if claim.kind == "hypothesis":
        reasons.append({"code": "hypothesis_not_established"})
    return {"claim_id": claim.claim_id, "statement": claim.statement, "scope": claim.scope,
            "kind": claim.kind, "depends_on": list(claim.depends_on), **relations,
            "status": status, "reasons": reasons}


def _assess_link(link: EvidenceLink, catalog: dict[str, Evidence], now: datetime) -> dict[str, object]:
    result: dict[str, object] = {"reference": link.reference, "expected_sha256": link.sha256,
                                "locator": link.locator}
    current = catalog.get(link.reference)
    if current is None:
        return {**result, "issues": ["missing_evidence"]}
    result["current"] = {"sha256": current.sha256, "kind": current.kind,
                         "observed_at": current.observed_at.isoformat()}
    issues = []
    if current.sha256 != link.sha256:
        issues.append("changed_evidence")
    if now < current.observed_at:
        issues.append("not_yet_observed")
    if current.expires_at is not None:
        result["current"]["expires_at"] = current.expires_at.isoformat()
        if now >= current.expires_at:
            issues.append("expired_evidence")
    return {**result, "issues": issues}


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evidence times must be timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def _digest(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("expected a lowercase SHA-256 digest")
    return value
