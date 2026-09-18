"""Evidence graph above immutable EvidencePacks. Packs are not rewritten here.

Timestamp is never freshness authority. Generations, project identity, and
state tokens are. Derived nodes name exact upstream ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from copilot.runtime.freshness import FreshnessClock, FreshnessDomain
from copilot.schemas.evidence import (
    CANONICAL_LIMITATIONS,
    INTERPRETIVE_KINDS,
    LIMITATION_ALIASES,
    MEASURED_KINDS,
    EvidenceKind,
    EvidencePack,
    FusionStatus,
    LimitationCode,
    ValidityStatus,
)


GOAL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "ENERGY_STRUCTURE": ("CAPTURE_MAIN", "FULLMIX_ANALYSIS"),
    "KICK_BASS_RELATIONSHIP": ("CAPTURE_SOURCES", "LOWEND_ANALYSIS"),
    "SOURCE_ACTIVITY": ("PROJECT_SNAPSHOT", "READ_ARRANGEMENT"),
    "HARMONIC_CONTEXT": ("READ_MIDI",),
    "DEVICE_CAUSAL_CONTEXT": ("READ_DEVICES", "READ_ROUTING"),
}

CAPABILITY_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "CAPTURE_MAIN": {
        "id_prefixes": ("ev.main.",),
        "names": ("main_capture",),
    },
    "FULLMIX_ANALYSIS": {
        "id_prefixes": ("ev.fullmix",),
        "names": ("fullmix_observation",),
    },
    "CAPTURE_SOURCES": {
        "id_prefixes": ("ev.source.",),
        "names": ("source_capture",),
    },
    "LOWEND_ANALYSIS": {
        "id_prefixes": ("ev.lowend",),
        "names": ("lowend_observation",),
    },
    "PROJECT_SNAPSHOT": {
        "id_prefixes": ("ev.project.",),
        "names": ("project_identity",),
        "kinds": ("STATE_TOKEN",),
    },
    "READ_ARRANGEMENT": {
        "id_prefixes": ("ev.arrangement",),
        "names": ("arrangement_state",),
    },
    "READ_MIDI": {
        "id_prefixes": ("ev.midi",),
        "names": ("midi_evidence",),
    },
    "READ_DEVICES": {
        "id_prefixes": ("ev.mixer", "ev.device"),
        "names": ("device_mixer_state",),
    },
    "READ_ROUTING": {
        "id_prefixes": ("ev.routing",),
        "names": ("routing",),
    },
}

DOMAIN_KIND_PREFIXES: dict[str, tuple[str, ...]] = {
    "ROUTING_STATE": ("routing", "off_mix", "capture_host"),
    "SEND_STATE": ("send", "off_mix", "capture_host"),
    "MONITORING_STATE": ("monitoring", "capture_host"),
    "DEVICE_PARAMETER_STATE": ("device", "tap", "capture_host"),
    "DEVICE_INVENTORY": ("device", "tap"),
    "PROJECT_TOPOLOGY": ("topology", "track"),
}

KNOWN_DOMAINS = frozenset(item.value for item in FreshnessDomain) | frozenset(
    DOMAIN_KIND_PREFIXES
)

LLM_PROVIDER_TOKENS = frozenset(
    {"astra", "llm", "gpt", "gemini", "openai", "anthropic", "claude"}
)

_PACK_STATE_DOMAINS: dict[str, tuple[str, ...]] = {
    "ev.routing": (FreshnessDomain.ROUTING_STATE.value,),
    "ev.mixer": (
        FreshnessDomain.DEVICE_INVENTORY.value,
        FreshnessDomain.DEVICE_PARAMETER_STATE.value,
    ),
    "ev.arrangement": (FreshnessDomain.ARRANGEMENT_STATE.value,),
    "ev.automation": (FreshnessDomain.AUTOMATION_STATE.value,),
    "ev.project": (FreshnessDomain.PROJECT_TOPOLOGY.value,),
}

_PACK_UPSTREAM: dict[str, tuple[str, ...]] = {
    "ev.fullmix": ("ev.main.capture",),
    "ev.lowend": ("ev.main.capture",),
}

_IMMUTABLE_PREFIXES = ("ev.main.", "ev.source.", "ev.fullmix", "ev.lowend", "pdsp.")


class ProjectIsolationError(ValueError):
    """Nodes from different project identities must not mix."""


class HallucinatedEvidenceError(ValueError):
    """An LLM result may never masquerade as a measurement."""


class StaleEvidenceError(ValueError):
    """Stale or rejected evidence cannot be treated as current."""


@dataclass
class ConfidenceComponents:
    """Separate axes. Do not invent a single percentage."""

    measurement_quality: str | None = None
    provider_confidence: float | None = None
    source_reliability: str | None = None
    cross_source_agreement: str | None = None
    reasoning_confidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "measurement_quality": self.measurement_quality,
            "provider_confidence": self.provider_confidence,
            "source_reliability": self.source_reliability,
            "cross_source_agreement": self.cross_source_agreement,
            "reasoning_confidence": self.reasoning_confidence,
            "combined": None,
        }


@dataclass
class FusionRecord:
    question: str
    node_ids: list[str]
    status: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "node_ids": list(self.node_ids),
            "status": self.status,
            "notes": self.notes,
        }


@dataclass
class EvidenceNode:
    identity: str
    kind: str
    project_state: str | None = None
    source_artifact: str | None = None
    provider: str | None = None
    version: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: str | None = None
    limitations: list[Any] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    subject_identity: str | None = None
    region: str | None = None
    time_scope: dict[str, Any] | None = None
    source_ref: str | None = None
    provider_version: str | None = None
    project_identity: str | None = None
    state_tokens: dict[str, str] = field(default_factory=dict)
    quality: str | None = None
    confidence_components: ConfidenceComponents | None = None
    freshness: dict[str, Any] | None = None
    validity: str = ValidityStatus.VALID.value
    artifact_hash: str | None = None
    freshness_domains: list[str] = field(default_factory=list)
    immutable_artifact: bool = False
    question: str | None = None

    def to_dict(self) -> dict[str, Any]:
        components = self.confidence_components
        return {
            "identity": self.identity,
            "evidence_id": self.identity,
            "kind": self.kind,
            "evidence_type": self.kind,
            "project_state": self.project_state,
            "source_artifact": self.source_artifact,
            "provider": self.provider,
            "version": self.version or self.provider_version,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "limitations": [dict(item) if isinstance(item, dict) else item for item in self.limitations],
            "dependencies": list(self.dependencies),
            "subject_identity": self.subject_identity,
            "region": self.region,
            "time_scope": self.time_scope,
            "source_ref": self.source_ref or self.source_artifact,
            "provider_version": self.provider_version or self.version,
            "project_identity": self.project_identity,
            "state_tokens": dict(self.state_tokens),
            "quality": self.quality,
            "confidence_components": None if components is None else components.to_dict(),
            "freshness": self.freshness,
            "validity": self.validity,
            "artifact_hash": self.artifact_hash,
            "freshness_domains": list(self.freshness_domains),
            "immutable_artifact": self.immutable_artifact,
            "question": self.question,
            "payload": self.payload,
        }

    def limitation_codes(self) -> set[str]:
        codes: set[str] = set()
        for item in self.limitations:
            if isinstance(item, dict) and item.get("code"):
                codes.add(str(item["code"]))
            elif isinstance(item, str) and item:
                codes.add(item)
        return codes

    def is_valid(self) -> bool:
        return self.validity == ValidityStatus.VALID.value

    def is_immutable(self) -> bool:
        return bool(self.immutable_artifact and self.artifact_hash)


@dataclass
class EvidenceGraph:
    project_identity: str | None = None
    project_state: str | None = None
    nodes: dict[str, EvidenceNode] = field(default_factory=dict)
    fusions: list[FusionRecord] = field(default_factory=list)
    clock: FreshnessClock | None = None

    def add(self, node: EvidenceNode) -> None:
        self._assert_not_hallucinated(node)
        identity = node.project_identity
        if self.project_identity and identity and identity != self.project_identity:
            raise ProjectIsolationError(
                f"PROJECT_MISMATCH: node {node.identity} is {identity}, "
                f"graph is {self.project_identity}"
            )
        if not self.project_identity and identity:
            self.project_identity = identity
        if not node.project_identity:
            node.project_identity = self.project_identity
        if not node.project_state:
            node.project_state = self.project_state
        node.limitations = merge_limitations(
            inherited_limitations(self, node.dependencies),
            node.limitations,
        )
        self.nodes[node.identity] = node

    def invalidate_for_domains(self, domains: list[str] | tuple[str, ...]) -> list[str]:
        """Drop state-dependent nodes. Immutable artifact-hashed analysis stays.

        Also cascades to derived nodes that name a dropped id as a dependency.
        Timestamp is never consulted.
        """
        wanted = {str(domain) for domain in domains}
        direct = [
            identity
            for identity, node in self.nodes.items()
            if self._domain_hit(node, wanted) and not node.is_immutable()
        ]
        stale = self._cascade_identities(direct)
        dropped: list[str] = []
        for identity in stale:
            if identity in self.nodes:
                del self.nodes[identity]
                dropped.append(identity)
        self.fusions = [
            record
            for record in self.fusions
            if not any(item in stale for item in record.node_ids)
        ]
        return dropped

    def mark_stale(self, identities: Iterable[str], *, reason: str = "UPSTREAM_STALE") -> list[str]:
        stale = self._cascade_identities(list(identities))
        marked: list[str] = []
        for identity in stale:
            node = self.nodes.get(identity)
            if node is None:
                continue
            node.validity = ValidityStatus.STALE.value
            node.freshness = {**(node.freshness or {}), "reason": reason}
            marked.append(identity)
        return marked

    def apply_freshness(self, clock: FreshnessClock | None = None) -> list[str]:
        """Reject stale tokens / generations. Timestamp is never authority."""
        clock = clock or self.clock
        if clock is None:
            return []
        if (
            clock.project_identity
            and self.project_identity
            and clock.project_identity != self.project_identity
        ):
            return self.reject_all("PROJECT_MISMATCH")
        changed: list[str] = []
        for node in self.nodes.values():
            if node.project_identity and clock.project_identity:
                if node.project_identity != clock.project_identity:
                    node.validity = ValidityStatus.REJECTED.value
                    node.freshness = {**(node.freshness or {}), "reason": "PROJECT_MISMATCH"}
                    changed.append(node.identity)
                    continue
            if node.is_immutable():
                continue
            if not self._tokens_match(node, clock) or not self._generations_match(node, clock):
                node.validity = ValidityStatus.STALE.value
                node.freshness = {**(node.freshness or {}), "reason": "STATE_TOKEN_OR_GENERATION"}
                changed.append(node.identity)
        if changed:
            self.mark_stale(changed, reason="UPSTREAM_STALE")
        return changed

    def reject_all(self, reason: str) -> list[str]:
        dropped = list(self.nodes)
        for node in self.nodes.values():
            node.validity = ValidityStatus.REJECTED.value
            node.freshness = {**(node.freshness or {}), "reason": reason}
        return dropped

    def switch_project(self, identity: str, token: str | None = None) -> list[str]:
        dropped: list[str] = []
        if self.project_identity and identity != self.project_identity:
            dropped = list(self.nodes)
            self.nodes.clear()
            self.fusions.clear()
        self.project_identity = identity
        self.project_state = token
        if self.clock is not None:
            self.clock.bind_project(identity, token)
        return dropped

    def bind_clock(self, clock: FreshnessClock) -> None:
        if (
            clock.project_identity
            and self.project_identity
            and clock.project_identity != self.project_identity
        ):
            raise ProjectIsolationError(
                f"PROJECT_MISMATCH: clock {clock.project_identity} "
                f"vs graph {self.project_identity}"
            )
        if not self.project_identity:
            self.project_identity = clock.project_identity
        if not self.project_state:
            self.project_state = clock.project_token
        self.clock = clock

    def require_valid(self, identity: str) -> EvidenceNode:
        node = self.nodes.get(identity)
        if node is None:
            raise StaleEvidenceError(f"SOURCE_MISSING: {identity}")
        if not node.is_valid():
            raise StaleEvidenceError(f"{node.validity}: {identity}")
        return node

    def fuse(self, question: str, node_ids: list[str] | None = None) -> FusionRecord:
        ids = list(node_ids or self._ids_for_question(question))
        present = [self.nodes[item] for item in ids if item in self.nodes]
        record = compare_nodes(question, present)
        self.fusions.append(record)
        agreement = record.status if record.status != FusionStatus.NOT_COMPARABLE.value else None
        for node in present:
            if node.confidence_components is None:
                node.confidence_components = ConfidenceComponents()
            node.confidence_components.cross_source_agreement = agreement
        return record

    def fuse_comparable(self) -> list[FusionRecord]:
        groups: dict[str, list[str]] = {}
        for node in self.nodes.values():
            if _kind_bucket(node.kind) == "interpretive":
                continue
            key = node.question or _default_question(node)
            groups.setdefault(key, []).append(node.identity)
        records: list[FusionRecord] = []
        for question, ids in groups.items():
            if len(ids) >= 2:
                records.append(self.fuse(question, ids))
        return records

    def record_provider_failure(
        self,
        *,
        provider: str,
        code: str = LimitationCode.MODEL_UNAVAILABLE.value,
        detail: str = "",
        subject_identity: str | None = None,
        region: str | None = None,
    ) -> EvidenceNode:
        node = EvidenceNode(
            identity=f"lim.provider.{_slug(provider)}.{code}",
            kind=EvidenceKind.LIMITATION.value,
            provider=provider,
            subject_identity=subject_identity,
            region=region,
            project_identity=self.project_identity,
            project_state=self.project_state,
            limitations=[{"code": canonicalize_limitation(code), "detail": detail}],
            payload={"ok": False, "provider": provider, "code": code},
            provenance={"kind": "provider_failure", "no_measurement": True},
            validity=ValidityStatus.VALID.value,
            quality="UNKNOWN",
        )
        self.add(node)
        return node

    def view(
        self,
        *,
        goals: tuple[str, ...] | list[str] | None = None,
        region: str | None = None,
        subjects: Iterable[str] | None = None,
        kinds: Iterable[str] | None = None,
        question: str | None = None,
        include_stale: bool = False,
    ) -> EvidenceView:
        wanted_subjects = {str(item) for item in subjects} if subjects is not None else None
        wanted_kinds = {str(item) for item in kinds} if kinds is not None else None
        scoped: dict[str, EvidenceNode] = {}
        for identity, node in self.nodes.items():
            if not include_stale and not node.is_valid():
                continue
            if region is not None and node.region not in {region, None}:
                continue
            if wanted_subjects is not None and node.subject_identity not in wanted_subjects:
                continue
            if wanted_kinds is not None and node.kind not in wanted_kinds:
                continue
            if question is not None and (node.question or _default_question(node)) != question:
                continue
            if goals is not None and not _node_covers_goals(node, goals):
                continue
            scoped[identity] = node
        support: list[str] = []
        counter: list[str] = []
        fusion_rows: list[dict[str, Any]] = []
        for record in self.fusions:
            if question is not None and record.question != question:
                continue
            if not any(item in scoped for item in record.node_ids):
                continue
            fusion_rows.append(record.to_dict())
            if record.status == FusionStatus.CONTRADICT.value:
                counter.extend(item for item in record.node_ids if item in scoped)
            elif record.status in {
                FusionStatus.AGREE.value,
                FusionStatus.PARTIALLY_AGREE.value,
            }:
                support.extend(item for item in record.node_ids if item in scoped)
            else:
                support.extend(item for item in record.node_ids if item in scoped)
        if not support:
            support = [
                identity
                for identity, node in scoped.items()
                if _kind_bucket(node.kind) != "interpretive"
            ]
        limitations = _collect_limitations(scoped.values())
        provenance = [
            {
                "evidence_id": node.identity,
                "provider": node.provider,
                "provider_version": node.provider_version or node.version,
                "source_ref": node.source_ref or node.source_artifact,
                "dependencies": list(node.dependencies),
            }
            for node in scoped.values()
        ]
        return EvidenceView(
            project_identity=self.project_identity,
            project_state=self.project_state,
            nodes={key: node.to_dict() for key, node in scoped.items()},
            summary={
                "node_count": len(scoped),
                "kinds": sorted({node.kind for node in scoped.values()}),
                "goals": list(goals) if goals is not None else [],
                "region": region,
                "include_stale": include_stale,
            },
            support=list(dict.fromkeys(support)),
            counterevidence=list(dict.fromkeys(counter)),
            limitations=limitations,
            provenance=provenance,
            fusions=fusion_rows,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_identity": self.project_identity,
            "project_state": self.project_state,
            "nodes": {key: node.to_dict() for key, node in self.nodes.items()},
            "fusions": [record.to_dict() for record in self.fusions],
        }

    def _domain_hit(self, node: EvidenceNode, wanted: set[str]) -> bool:
        deps = {str(item) for item in node.dependencies}
        if wanted & deps:
            return True
        if wanted & {str(item) for item in node.freshness_domains}:
            return True
        kind = str(node.kind or "").lower()
        for domain in wanted:
            for prefix in DOMAIN_KIND_PREFIXES.get(domain, ()):
                if prefix in kind:
                    return True
        return False

    def _cascade_identities(self, seeds: list[str]) -> list[str]:
        stale = list(dict.fromkeys(seeds))
        known = set(stale)
        changed = True
        while changed:
            changed = False
            for identity, node in self.nodes.items():
                if identity in known:
                    continue
                if any(dep in known for dep in node.dependencies if dep not in KNOWN_DOMAINS):
                    stale.append(identity)
                    known.add(identity)
                    changed = True
        return stale

    def _ids_for_question(self, question: str) -> list[str]:
        return [
            node.identity
            for node in self.nodes.values()
            if (node.question or _default_question(node)) == question
        ]

    @staticmethod
    def _tokens_match(node: EvidenceNode, clock: FreshnessClock) -> bool:
        token = node.state_tokens.get("project") or node.project_state
        if token and clock.project_token and token != clock.project_token:
            return False
        return True

    @staticmethod
    def _generations_match(node: EvidenceNode, clock: FreshnessClock) -> bool:
        generations = (node.freshness or {}).get("generations") or {}
        for domain, gen in generations.items():
            try:
                current = clock.current(FreshnessDomain(domain))
            except ValueError:
                continue
            if int(gen) != int(current):
                return False
        return True

    @staticmethod
    def _assert_not_hallucinated(node: EvidenceNode) -> None:
        if not _is_llm_provider(node.provider):
            return
        if str(node.kind) == EvidenceKind.MEASUREMENT.value:
            raise HallucinatedEvidenceError(
                f"LLM provider {node.provider!r} cannot create MEASUREMENT {node.identity}"
            )


@dataclass
class EvidenceView:
    """What Astra sees. Not parent-pack → child-pack navigation."""

    project_identity: str | None
    project_state: str | None
    nodes: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    support: list[str] = field(default_factory=list)
    counterevidence: list[str] = field(default_factory=list)
    limitations: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    fusions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_identity": self.project_identity,
            "project_state": self.project_state,
            "nodes": self.nodes,
            "summary": self.summary,
            "support": list(self.support),
            "counterevidence": list(self.counterevidence),
            "limitations": list(self.limitations),
            "provenance": list(self.provenance),
            "fusions": list(self.fusions),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceView:
        """Rebuild a view from graph.view().to_dict() / blackboard JSON."""
        return cls(
            project_identity=payload.get("project_identity"),
            project_state=payload.get("project_state"),
            nodes=dict(payload.get("nodes") or {}),
            summary=dict(payload.get("summary") or {}),
            support=list(payload.get("support") or []),
            counterevidence=list(payload.get("counterevidence") or []),
            limitations=list(payload.get("limitations") or []),
            provenance=list(payload.get("provenance") or []),
            fusions=list(payload.get("fusions") or []),
        )


def requirements_for_goals(
    goals: tuple[str, ...] | list[str],
    graph: EvidenceGraph | None = None,
) -> list[str]:
    """Minimum acquisition capabilities. Not an execution sequence."""
    needed: list[str] = []
    seen: set[str] = set()
    for goal in goals:
        for capability_id in GOAL_CAPABILITIES.get(goal, ()):
            if capability_id not in seen:
                seen.add(capability_id)
                needed.append(capability_id)
    if graph is None:
        return needed
    inventory = inventory_for_goals(goals, graph)
    missing = set(inventory["missing_capabilities"])
    return [item for item in needed if item in missing]


def inventory_for_goals(
    goals: tuple[str, ...] | list[str],
    graph: EvidenceGraph | None = None,
) -> dict[str, Any]:
    """Existing / valid / stale / missing. No acquisition order."""
    required = []
    seen: set[str] = set()
    for goal in goals:
        for capability_id in GOAL_CAPABILITIES.get(goal, ()):
            if capability_id not in seen:
                seen.add(capability_id)
                required.append(capability_id)
    existing: list[str] = []
    valid: list[str] = []
    stale: list[str] = []
    covered: set[str] = set()
    if graph is not None:
        for capability_id in required:
            matches = [
                node
                for node in graph.nodes.values()
                if _covers_capability(node, capability_id)
            ]
            for node in matches:
                existing.append(node.identity)
                if node.is_valid():
                    valid.append(node.identity)
                    covered.add(capability_id)
                else:
                    stale.append(node.identity)
    missing_capabilities = [item for item in required if item not in covered]
    return {
        "existing": list(dict.fromkeys(existing)),
        "valid": list(dict.fromkeys(valid)),
        "stale": list(dict.fromkeys(stale)),
        "missing": list(missing_capabilities),
        "missing_capabilities": list(missing_capabilities),
        "required_capabilities": list(required),
        "acquisition": {
            "capabilities": list(missing_capabilities),
            "sequencing": None,
        },
    }


def graph_from_pack(
    pack: EvidencePack | dict[str, Any],
    *,
    project_identity: str | None = None,
    project_state: str | None = None,
    provider: str | None = None,
    provider_version: str | None = None,
    clock: FreshnessClock | None = None,
) -> EvidenceGraph:
    """Adapter. Existing pack JSON stays readable and is not rewritten."""
    model = _coerce_pack(pack)
    identity = project_identity or _pack_project_identity(model)
    state = project_state or model.project_token
    graph = EvidenceGraph(project_identity=identity, project_state=state, clock=clock)
    pack_limitations = [normalize_limitation(row) for row in model.limitations]
    present_ids = {item.evidence_id for item in model.items}
    for item in model.items:
        node = _node_from_item(
            item,
            pack=model,
            project_identity=identity,
            provider=provider,
            provider_version=provider_version,
            pack_limitations=pack_limitations,
            present_ids=present_ids,
        )
        graph.add(node)
    for index, limitation in enumerate(pack_limitations):
        graph.add(
            EvidenceNode(
                identity=f"lim.pack.{index}.{limitation['code']}",
                kind=EvidenceKind.LIMITATION.value,
                project_identity=identity,
                project_state=state,
                region=model.region,
                provider=provider or "evidence_pack_v1",
                version=provider_version or model.analysis_version,
                provider_version=provider_version or model.analysis_version,
                limitations=[limitation],
                payload=limitation,
                provenance={"source": "pack.limitations", "pack_id": model.pack_id},
                state_tokens=_pack_tokens(model),
                question=limitation["code"],
            )
        )
    repropagate_limitations(graph)
    if clock is not None:
        graph.bind_clock(clock)
        graph.apply_freshness(clock)
    return graph


def repropagate_limitations(graph: EvidenceGraph) -> None:
    changed = True
    while changed:
        changed = False
        for node in graph.nodes.values():
            merged = merge_limitations(
                inherited_limitations(graph, node.dependencies),
                node.limitations,
            )
            before = [row["code"] for row in node.limitations if isinstance(row, dict)]
            after = [row["code"] for row in merged]
            if after != before:
                node.limitations = merged
                changed = True


def canonicalize_limitation(code: str) -> str:
    aliased = LIMITATION_ALIASES.get(code)
    if aliased is not None:
        return aliased.value
    if code in CANONICAL_LIMITATIONS:
        return code
    return code


def normalize_limitation(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        item = item.model_dump()
    if isinstance(item, str):
        original = item
        code = canonicalize_limitation(item)
        row = {"code": code, "detail": ""}
        if original != code:
            row["original_code"] = original
        return row
    if isinstance(item, dict):
        original = str(item.get("code") or "")
        row = dict(item)
        row["code"] = canonicalize_limitation(original)
        if original and original != row["code"]:
            row["original_code"] = original
        return row
    return {"code": str(item), "detail": ""}


def merge_limitations(*groups: Iterable[Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            row = normalize_limitation(item)
            key = row["code"]
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def inherited_limitations(graph: EvidenceGraph, dependencies: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dep in dependencies:
        upstream = graph.nodes.get(str(dep))
        if upstream is None:
            continue
        rows.extend(upstream.limitations)
    return rows


def compare_nodes(question: str, nodes: list[EvidenceNode]) -> FusionRecord:
    ids = [node.identity for node in nodes]
    if len(nodes) < 2:
        return FusionRecord(
            question=question,
            node_ids=ids,
            status=FusionStatus.NOT_COMPARABLE.value,
            notes="need two or more sources",
        )
    buckets = {_kind_bucket(node.kind) for node in nodes}
    if "interpretive" in buckets and "measured" in buckets:
        return FusionRecord(
            question=question,
            node_ids=ids,
            status=FusionStatus.NOT_COMPARABLE.value,
            notes="interpretation cannot be compared as measurement",
        )
    values = [_comparable_value(node) for node in nodes]
    types = {type(value).__name__ for value in values}
    if not _types_comparable(values):
        return FusionRecord(
            question=question,
            node_ids=ids,
            status=FusionStatus.NOT_COMPARABLE.value,
            notes=f"incomparable value types: {sorted(types)}",
        )
    if all(value == values[0] for value in values[1:]):
        status = FusionStatus.AGREE.value
        notes = "identical values"
    elif _all_numeric(values):
        status, notes = _numeric_agreement(values)
    elif _all_bool(values):
        status = (
            FusionStatus.AGREE.value
            if all(value == values[0] for value in values[1:])
            else FusionStatus.CONTRADICT.value
        )
        notes = "boolean comparison"
    else:
        status = FusionStatus.CONTRADICT.value
        notes = "values differ"
    return FusionRecord(question=question, node_ids=ids, status=status, notes=notes)


def snapshot_freshness(
    clock: FreshnessClock,
    domains: Iterable[str],
    track: int | None = None,
) -> dict[str, Any]:
    generations: dict[str, int] = {}
    for domain in domains:
        try:
            generations[str(domain)] = clock.current(FreshnessDomain(domain), track)
        except ValueError:
            continue
    return {
        "project_identity": clock.project_identity,
        "project_token": clock.project_token,
        "epoch": clock.epoch,
        "generations": generations,
    }


def _coerce_pack(pack: EvidencePack | dict[str, Any]) -> EvidencePack:
    if isinstance(pack, EvidencePack):
        return pack
    if isinstance(pack, dict) and "pack" in pack and isinstance(pack["pack"], dict):
        return EvidencePack.model_validate(pack["pack"])
    return EvidencePack.model_validate(pack)


def _pack_project_identity(pack: EvidencePack) -> str:
    for item in pack.items:
        if item.evidence_id == "ev.project.identity":
            return str(item.value)
    return pack.project_token


def _pack_tokens(pack: EvidencePack) -> dict[str, str]:
    tokens = {"project": pack.project_token, "audible": pack.audible_token}
    if pack.target_token:
        tokens["target"] = pack.target_token
    return tokens


def _artifact_hash_from_item(item: Any, payload: Any) -> str | None:
    """DSP items store the hash as source_ref artifact:<sha>; packs use payload keys."""
    if isinstance(payload, dict):
        hashed = payload.get("audio_sha256") or payload.get("analyzer_sha256") or payload.get(
            "source_artifact_hash"
        )
        if hashed:
            return str(hashed)
    ref = str(getattr(item, "source_ref", "") or "")
    if ref.startswith("artifact:"):
        digest = ref.split(":", 1)[1].strip()
        return digest or None
    return None


def _node_from_item(
    item: Any,
    *,
    pack: EvidencePack,
    project_identity: str,
    provider: str | None,
    provider_version: str | None,
    pack_limitations: list[dict[str, Any]],
    present_ids: set[str],
) -> EvidenceNode:
    payload = item.value if isinstance(item.value, dict) else {"value": item.value}
    artifact_hash = _artifact_hash_from_item(item, payload)
    deps = [
        dep
        for dep in _PACK_UPSTREAM.get(item.evidence_id, ())
        if dep in present_ids
    ]
    if item.evidence_id == "ev.lowend":
        deps.extend(
            ident
            for ident in sorted(present_ids)
            if ident.startswith("ev.source.") and ident.endswith(".capture")
        )
    domains = list(_PACK_STATE_DOMAINS.get(item.evidence_id, ()))
    immutable = item.evidence_id.startswith(_IMMUTABLE_PREFIXES) and bool(artifact_hash)
    tokens = _pack_tokens(pack)
    if item.project_token:
        tokens["project"] = item.project_token
    if item.audible_token:
        tokens["audible"] = item.audible_token
    if item.target_token:
        tokens["target"] = item.target_token
    own_limits = [normalize_limitation(code) for code in item.limitations]
    return EvidenceNode(
        identity=item.evidence_id,
        kind=str(item.kind.value if hasattr(item.kind, "value") else item.kind),
        project_state=pack.project_token,
        source_artifact=item.source_ref,
        source_ref=item.source_ref,
        provider=provider or item.source_ref,
        version=provider_version or item.analysis_version,
        provider_version=provider_version or item.analysis_version,
        provenance={
            "pack_id": pack.pack_id,
            "analysis_version": item.analysis_version,
            "name": item.name,
        },
        confidence=None if item.confidence is None else str(item.confidence),
        limitations=merge_limitations(pack_limitations, own_limits),
        dependencies=deps,
        payload=payload,
        subject_identity=item.source_ref,
        region=item.region or pack.region,
        time_scope={"region": item.region or pack.region},
        project_identity=project_identity,
        state_tokens=tokens,
        quality=str(item.quality.value if hasattr(item.quality, "value") else item.quality),
        confidence_components=ConfidenceComponents(
            measurement_quality=str(
                item.quality.value if hasattr(item.quality, "value") else item.quality
            ),
            provider_confidence=item.confidence,
            source_reliability=item.source_ref,
        ),
        artifact_hash=None if artifact_hash is None else str(artifact_hash),
        freshness_domains=domains,
        immutable_artifact=immutable,
        question=item.name,
    )


def _covers_capability(node: EvidenceNode, capability_id: str) -> bool:
    if node.kind == EvidenceKind.LIMITATION.value:
        return False
    markers = CAPABILITY_MARKERS.get(capability_id) or {}
    for prefix in markers.get("id_prefixes", ()):
        if node.identity.startswith(prefix):
            return True
    name = str((node.payload or {}).get("name") or node.question or "")
    if name in set(markers.get("names", ())):
        return True
    if node.kind in set(markers.get("kinds", ())) and node.identity.startswith("ev."):
        return True
    return False


def _node_covers_goals(node: EvidenceNode, goals: Iterable[str]) -> bool:
    capabilities = {
        capability
        for goal in goals
        for capability in GOAL_CAPABILITIES.get(goal, ())
    }
    if node.kind == EvidenceKind.LIMITATION.value:
        return True
    return any(_covers_capability(node, capability) for capability in capabilities)


def _collect_limitations(nodes: Iterable[EvidenceNode]) -> list[dict[str, Any]]:
    return merge_limitations(*(node.limitations for node in nodes))


def _is_llm_provider(provider: str | None) -> bool:
    if not provider:
        return False
    tokens = {token for token in _slug(provider).split("-") if token}
    return bool(tokens & LLM_PROVIDER_TOKENS)


def _slug(value: str) -> str:
    out: list[str] = []
    for char in value.lower():
        if char.isalnum():
            out.append(char)
        else:
            out.append("-")
    return "".join(out).strip("-")


def _kind_bucket(kind: str) -> str:
    try:
        enumerated = EvidenceKind(kind)
    except ValueError:
        lowered = kind.lower()
        if any(token in lowered for token in ("routing", "midi", "device", "fact", "capture")):
            return "measured"
        return "other"
    if enumerated in INTERPRETIVE_KINDS:
        return "interpretive"
    if enumerated is EvidenceKind.LIMITATION:
        return "limitation"
    if enumerated in MEASURED_KINDS:
        return "measured"
    return "other"


def _default_question(node: EvidenceNode) -> str:
    name = (node.payload or {}).get("name")
    if name:
        return str(name)
    return f"{node.kind}:{node.subject_identity or ''}:{node.region or ''}"


def _comparable_value(node: EvidenceNode) -> Any:
    payload = node.payload or {}
    if "value" in payload and not isinstance(payload["value"], dict):
        return payload["value"]
    for key in ("rms", "peak", "event_count", "ok", "active_count", "audio_sha256"):
        if key in payload:
            return payload[key]
    return payload


def _types_comparable(values: list[Any]) -> bool:
    if _all_numeric(values) or _all_bool(values):
        return True
    kinds = {type(value) for value in values}
    return len(kinds) == 1


def _all_numeric(values: list[Any]) -> bool:
    return all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)


def _all_bool(values: list[Any]) -> bool:
    return all(isinstance(value, bool) for value in values)


def _numeric_agreement(values: list[Any]) -> tuple[str, str]:
    nums = [float(value) for value in values]
    lo, hi = min(nums), max(nums)
    if lo == hi:
        return FusionStatus.AGREE.value, "identical numbers"
    span = abs(hi - lo)
    scale = max(abs(hi), abs(lo), 1e-12)
    if span / scale <= 0.05:
        return FusionStatus.AGREE.value, "within 5%"
    if lo == 0 or hi == 0:
        return FusionStatus.CONTRADICT.value, "zero vs nonzero"
    if (lo > 0) == (hi > 0) and (hi / lo if lo != 0 else 0) < 10:
        return FusionStatus.PARTIALLY_AGREE.value, "same direction, different magnitude"
    return FusionStatus.CONTRADICT.value, "numeric conflict"
