"""Operator contract — the core abstraction every mutation goes through (F-4, F-5).

Every operator in piano_core (SplineTransfer, AnchorInterpolate, PhysicalExpand,
ToneIdentifyAndCorrect, BankIntegrity, ...) must:

  1. Take parameters that subclass `OperatorParams` — which always carries
     `random_seed: int` (F-4) so stochastic steps are deterministic.
  2. Return an `ApplyResult(bank, edit, diagnostics)` (F-5) — never a bare Bank.
     This lets the system build an auditable chain (parent/child Banks, edit
     history for deterministic replay in i5).

The Operator ABC is intentionally minimal — `validate()` and operator-specific
helpers live on subclasses. Diagnostics use a tag-based union via subclassing
`ApplyDiagnostics` (each operator narrows the shape in its own module).
"""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Generic, TypeVar

from piano_core.models.bank import Bank


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Operator params (F-4) — random_seed is required, everything else optional.
# Subclasses add their own fields; the base class enforces the seed contract.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OperatorParams:
    """Base for all operator-specific parameter containers.

    F-4: every operator has an explicit `random_seed`. Stochastic steps
    (bootstrap, Huber MAD init, etc.) must consume it — either directly or
    by deriving child seeds via ``numpy.random.default_rng(seed).integers(...)``.
    """

    random_seed: int = 0

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable representation for edit_history and API replay."""
        return asdict(self)


P = TypeVar("P", bound=OperatorParams)


# ---------------------------------------------------------------------------
# Diagnostics — structured per-operator observability.
# Each operator subclasses and adds its own fields (e.g. per_partial_log in i3).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ApplyDiagnostics:
    """Base diagnostics. Operators extend with operator-specific fields."""

    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# EditRecord — append-only audit entry persisted with the bank.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EditRecord:
    """One entry in a Bank's edit history — what operator ran, with what params, when."""

    operator: str
    params: dict[str, Any]
    timestamp: datetime
    source_note_id: tuple[int, int] | None = None
    edit_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @classmethod
    def now(
        cls,
        *,
        operator: str,
        params: OperatorParams | dict[str, Any],
        source_note_id: tuple[int, int] | None = None,
    ) -> "EditRecord":
        """Factory that stamps the current UTC timestamp and generates a fresh edit_id."""
        params_dict = params.as_dict() if isinstance(params, OperatorParams) else dict(params)
        return cls(
            operator=operator,
            params=params_dict,
            timestamp=datetime.now(timezone.utc),
            source_note_id=source_note_id,
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form (ISO timestamp, tuple -> list)."""
        return {
            "edit_id": self.edit_id,
            "operator": self.operator,
            "params": self.params,
            "timestamp": self.timestamp.isoformat(),
            "source_note_id": list(self.source_note_id) if self.source_note_id else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EditRecord":
        """Parse an ISO-formatted JSON dict back into an EditRecord (i5 replay)."""
        ts_raw = d["timestamp"]
        ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else ts_raw
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        src = d.get("source_note_id")
        return cls(
            edit_id=d.get("edit_id", str(uuid.uuid4())),
            operator=d["operator"],
            params=dict(d.get("params") or {}),
            timestamp=ts,
            source_note_id=tuple(src) if src is not None else None,
        )


# ---------------------------------------------------------------------------
# ApplyResult (F-5) — canonical return value for every Operator.apply() call.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ApplyResult:
    """What every operator returns. Never a bare Bank — this contract is load-bearing."""

    bank: Bank
    edit: EditRecord
    diagnostics: ApplyDiagnostics

    def to_summary(self) -> dict[str, Any]:
        """Compact JSON view for API responses (full bank omitted)."""
        return {
            "new_bank_id": self.bank.id,
            "parent_id": self.bank.parent_id,
            "edit": self.edit.as_dict(),
            "diagnostics": self.diagnostics.as_dict(),
        }


# ---------------------------------------------------------------------------
# Operator ABC — subclass to add custom operators.
# ---------------------------------------------------------------------------
class Operator(ABC, Generic[P]):
    """Abstract base for operators. All mutations on Bank go through this contract."""

    # Short stable identifier used in EditRecord.operator and routing.
    name: ClassVar[str]
    # The params dataclass this operator consumes. Subclasses set this.
    params_class: ClassVar[type[OperatorParams]]

    @abstractmethod
    def apply(self, bank: Bank, params: P) -> ApplyResult:
        """Run the operator. Must return ApplyResult — no bare Bank allowed (F-5)."""

    # Optional: operators may override to pre-validate params before running apply.
    def validate(self, bank: Bank, params: P) -> None:
        return None


# ---------------------------------------------------------------------------
# Registry — simple name→class lookup populated by operator modules.
# i5 deterministic replay uses this to resolve EditRecord.operator back to a class.
# ---------------------------------------------------------------------------
class OperatorRegistry:
    _registry: ClassVar[dict[str, type[Operator]]] = {}

    @classmethod
    def register(cls, operator_cls: type[Operator]) -> type[Operator]:
        if not hasattr(operator_cls, "name") or not operator_cls.name:
            raise ValueError(f"{operator_cls.__name__} must define a non-empty `name`")
        if operator_cls.name in cls._registry:
            raise ValueError(f"operator {operator_cls.name!r} is already registered")
        cls._registry[operator_cls.name] = operator_cls
        logger.debug("operator.register", extra={"name": operator_cls.name})
        return operator_cls

    @classmethod
    def get(cls, name: str) -> type[Operator]:
        if name not in cls._registry:
            raise KeyError(f"unknown operator: {name!r}")
        return cls._registry[name]

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(sorted(cls._registry))

    @classmethod
    def clear(cls) -> None:
        """For tests only."""
        cls._registry.clear()


# ---------------------------------------------------------------------------
# Small helper — json-safe dump of an ApplyResult.
# ---------------------------------------------------------------------------
def apply_result_to_json(result: ApplyResult) -> str:
    return json.dumps(result.to_summary(), ensure_ascii=False, sort_keys=True)
