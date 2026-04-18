"""Pydantic response schemas for the piano_web API.

Kept separate from the domain model (`piano_core`) so the HTTP contract can
evolve (additions, renames, compact views) without touching the math/storage
layer. Translation helpers live at the bottom of the file.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from piano_core.constants import MATH_PARAMS, Origin, STORAGE_PARAMS
from piano_core.models.anchor import Anchor
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


# ---------------------------------------------------------------------------
# Bank schemas
# ---------------------------------------------------------------------------

class BankSummary(BaseModel):
    """Listing row — compact, no partials. Backed by the `banks` table projection."""

    id: str
    parent_id: str | None
    instrument: str | None
    created_at: str | None


class BankDetail(BaseModel):
    """Detail view — metadata + note index. Partials fetched per-note."""

    id: str
    parent_id: str | None
    instrument: str | None
    n_notes: int
    velocities: list[int]
    midi_range: list[int] | None
    k_max: int | None
    created_at: str | None
    source: str | None
    metadata: dict[str, Any]


class NoteIndex(BaseModel):
    """One entry in /api/banks/:id/notes."""

    midi: int
    velocity: int


# ---------------------------------------------------------------------------
# Partial / Note detail
# ---------------------------------------------------------------------------

class PartialDetail(BaseModel):
    k: int
    f_hz: float
    A0: float
    tau1: float
    tau2: float
    a1: float
    beat_hz: float
    phi: float
    fit_quality: float
    sigma: float | None = None
    origin: Origin = "measured"


class NoteDetail(BaseModel):
    midi: int
    velocity: int
    f0_hz: float
    B: float
    phi_diff: float
    attack_tau: float
    A_noise: float
    noise_centroid_hz: float
    rms_gain: float
    n_strings: int | None = None
    rise_tau: float | None = None
    stereo_width: float | None = None
    partials: list[PartialDetail]


# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------

class CurvePoint(BaseModel):
    k: int
    value: float
    sigma: float | None
    fit_quality: float
    origin: Origin


class CurvesPayload(BaseModel):
    """Pre-computed per-parameter curves for the plot canvas.

    `parameters` is keyed by name in `MATH_PARAMS`:
      - Storage params (`tau1`, `tau2`, `A0`, `a1`, `beat_hz`) map directly to
        Partial fields.
      - Derived `f_coef` is computed on demand as ``f_hz/(k*f0*sqrt(1+B*k^2)) - 1``.
    """

    midi: int
    velocity: int
    parameters: dict[str, list[CurvePoint]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Translation helpers: domain -> schema
# ---------------------------------------------------------------------------

def bank_summary_from_row(row: dict[str, Any]) -> BankSummary:
    """Map a `banks` SELECT row (dict) into a BankSummary."""
    return BankSummary(
        id=row["id"],
        parent_id=row.get("parent_id"),
        instrument=row.get("instrument"),
        created_at=row.get("created_at"),
    )


def bank_detail_from_domain(bank: Bank) -> BankDetail:
    s = bank.summary()
    return BankDetail(
        id=bank.id,
        parent_id=bank.parent_id,
        instrument=s["instrument"],
        n_notes=s["n_notes"],
        velocities=list(s["velocities"]),
        midi_range=s["midi_range"],
        k_max=s["k_max"],
        created_at=s["created_at"],
        source=s["source"],
        metadata=dict(bank.metadata),
    )


def partial_from_domain(p: Partial) -> PartialDetail:
    return PartialDetail(
        k=p.k, f_hz=p.f_hz, A0=p.A0, tau1=p.tau1, tau2=p.tau2, a1=p.a1,
        beat_hz=p.beat_hz, phi=p.phi, fit_quality=p.fit_quality,
        sigma=p.sigma, origin=p.origin,
    )


def note_detail_from_domain(note: Note) -> NoteDetail:
    return NoteDetail(
        midi=note.midi,
        velocity=note.vel,
        f0_hz=note.f0_hz,
        B=note.B,
        phi_diff=note.phi_diff,
        attack_tau=note.attack_tau,
        A_noise=note.A_noise,
        noise_centroid_hz=note.noise_centroid_hz,
        rms_gain=note.rms_gain,
        n_strings=note.n_strings,
        rise_tau=note.rise_tau,
        stereo_width=note.stereo_width,
        partials=[partial_from_domain(p) for p in note.partials],
    )


def _f_coef_from_partial(p: Partial, *, f0: float, B: float) -> float | None:
    """Compute the dimensionless inharmonicity residual f_hz/(k*f0*sqrt(1+B*k^2)) - 1.

    Returns None when the denominator collapses to zero (unusable — no meaningful
    residual for a partial at k=0 or with f0=0).
    """
    denom = p.k * f0 * math.sqrt(1.0 + B * p.k * p.k)
    if denom <= 0:
        return None
    return p.f_hz / denom - 1.0


# ---------------------------------------------------------------------------
# Anchor schemas (i2)
# ---------------------------------------------------------------------------

AnchorOrigin = Literal["manual", "imported", "regression_fit"]


class AnchorDetail(BaseModel):
    """Response view for a single anchor."""

    id: str
    midi: int
    velocity: int
    k: int
    parameter: str
    value: float
    weight: float = Field(..., ge=0.0, le=1.0)
    origin: AnchorOrigin
    created_at: str
    created_by: str
    note: str


class AnchorCreate(BaseModel):
    """Body for POST /api/banks/:id/notes/:m/:v/anchors."""

    k: int = Field(..., ge=1)
    parameter: Literal["tau1", "tau2", "A0", "a1", "beat_hz", "f_coef"]
    value: float
    weight: float = Field(0.5, ge=0.0, le=1.0)
    origin: AnchorOrigin = "manual"
    created_by: str = "user"
    note: str = ""


class AnchorPatch(BaseModel):
    """Body for PATCH /api/banks/:id/anchors/:aid — all fields optional."""

    value: float | None = None
    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = None


class AnchorMutationResponse(BaseModel):
    """Wrap every mutation's response so the client can chain new_bank_id."""

    new_bank_id: str
    parent_id: str | None
    anchor: AnchorDetail | None = None   # None on DELETE


# ---------------------------------------------------------------------------
# Anchor-interpolate op
# ---------------------------------------------------------------------------

class AnchorInterpolateParams(BaseModel):
    """POST /api/ops/anchor-interpolate body."""

    target_note_ids: list[tuple[int, int]] = Field(
        ..., description="(midi, velocity) tuples to evaluate the pipeline on.",
    )
    parameters: list[Literal["tau1", "tau2", "A0", "a1", "beat_hz", "f_coef"]]
    prior_weight: float = Field(0.3, ge=0.0, le=1.0)
    smoothing: float | None = Field(None, ge=0.0)
    k_range: tuple[int, int] | None = None
    commit: bool = False
    random_seed: int = 0


class ParameterCurveDiag(BaseModel):
    """Per (note, parameter) diagnostics + sampled curve."""

    midi: int
    velocity: int
    parameter: str
    k_grid: list[int]
    values: list[float]
    sigmas: list[float]
    lambda_used: float
    used_pchip: bool
    coverage: tuple[int, int]
    n_observations: int
    n_anchors_used: int
    warnings: list[str]


class AnchorInterpolateResponse(BaseModel):
    """Response for the anchor-interpolate op.

    When ``commit=True``, ``new_bank_id`` is set to the freshly-persisted bank;
    when ``commit=False``, it is None (preview only).
    """

    new_bank_id: str | None = None
    parent_id: str | None = None
    per_parameter: list[ParameterCurveDiag]


# ---------------------------------------------------------------------------
# i3: ToneIdentifyAndCorrect + DeviationReport schemas
# ---------------------------------------------------------------------------

class ToneCorrectParamsIn(BaseModel):
    """Body for POST /api/ops/tone-identify-and-correct (and /tone-identify-only)."""

    target_note_id: tuple[int, int]
    reference_bank_ids: list[str] = Field(default_factory=list)
    parameters: list[Literal["tau1", "tau2", "A0", "a1", "beat_hz", "f_coef"]] = Field(
        default_factory=lambda: ["tau1", "tau2", "A0", "a1", "beat_hz"],
    )
    use_anchors: bool = True
    use_physical_prior: bool = True
    preserve_fundamental: bool = True
    noise_threshold_d: float = Field(1.0, ge=0.0)
    correction_threshold_d: float = Field(2.5, ge=0.0)
    fill_quality_threshold: float = Field(0.3, ge=0.0, le=1.0)
    fallback_on_insufficient: Literal["error", "skip", "prior_only"] = "prior_only"
    min_sources_for_consensus: int = Field(2, ge=1)
    commit: bool = False
    random_seed: int = 0


class ToneIdentifyOnlyResponse(BaseModel):
    """Phase A only — returns the TonalReference summary.

    Clients use this to preview the consensus curves and quality scores
    before committing any correction.
    """

    target_note_id: tuple[int, int]
    reference_bank_ids: list[str]
    reference_summary: dict[str, Any]


class ToneCorrectResponse(BaseModel):
    """Full correction response.

    `new_bank_id` is populated only when ``commit=True``; otherwise it is
    None and the caller has received a preview.
    """

    new_bank_id: str | None
    parent_id: str | None
    target_note_id: tuple[int, int]
    reference_bank_ids: list[str]
    reference_summary: dict[str, Any]
    per_partial_log: list[dict[str, Any]]
    n_changed: int
    n_filled: int
    n_unchanged: int
    warnings: list[str]


# ---- Deviation report ---------------------------------------------------

class DeviationEntryOut(BaseModel):
    midi: int
    velocity: int
    k: int
    parameter: str
    target_value: float
    reference_value: float
    reference_sigma: float
    z_score: float
    recommend_action: str


class DeviationReportResponse(BaseModel):
    target_bank_id: str
    reference_bank_ids: list[str]
    loo: bool
    min_z: float
    parameters: list[str]
    entries: list[DeviationEntryOut]
    n_entries: int


# ---------------------------------------------------------------------------
# SplineTransfer (i3.6)
# ---------------------------------------------------------------------------

TransferModeLiteral = Literal["absolute", "relative", "delta"]


class SplineTransferConfig(BaseModel):
    """One row in the multi-parameter transfer plan."""

    parameter: Literal["tau1", "tau2", "A0", "a1", "beat_hz", "f_coef"]
    mode: TransferModeLiteral
    preserve_fundamental: bool = True
    clamp_to_bounds: bool = True
    source_smoothing: float | None = Field(default=None, ge=0.0)


class SplineTransferParamsIn(BaseModel):
    """Body for POST /api/ops/spline-transfer.

    The `source_bank_id` defaults to the target bank (self-transfer) when
    omitted. That matches the common case where a user wants to project one
    note's shape onto neighbouring notes in the *same* bank.
    """

    source_bank_id: str | None = None
    source_note_id: tuple[int, int]
    target_note_ids: list[tuple[int, int]]
    parameter_configs: list[SplineTransferConfig] | None = None

    # Legacy one-shot API — honoured when parameter_configs is absent.
    legacy_parameter: Literal["tau1", "tau2", "A0", "a1", "beat_hz", "f_coef"] | None = None
    legacy_mode: TransferModeLiteral | None = None

    commit: bool = False
    random_seed: int = 0


class SplineTransferResponse(BaseModel):
    new_bank_id: str | None
    parent_id: str | None
    source_bank_id: str
    source_note_id: tuple[int, int]
    target_note_ids: list[tuple[int, int]]
    parameter_configs: list[SplineTransferConfig]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Anchor translation helpers
# ---------------------------------------------------------------------------

def anchor_detail_from_domain(a: Anchor) -> AnchorDetail:
    return AnchorDetail(
        id=a.id,
        midi=a.midi,
        velocity=a.velocity,
        k=a.k,
        parameter=a.parameter,
        value=a.value,
        weight=a.weight,
        origin=a.origin,
        created_at=a.created_at.isoformat(),
        created_by=a.created_by,
        note=a.note,
    )


def curves_from_note(
    note: Note,
    *,
    parameters: list[str] | None = None,
) -> CurvesPayload:
    """Build a CurvesPayload for `note`, restricted to `parameters` (default: all MATH_PARAMS)."""
    requested = tuple(parameters or MATH_PARAMS)
    result: dict[str, list[CurvePoint]] = {}
    for param in requested:
        if param not in MATH_PARAMS:
            continue
        points: list[CurvePoint] = []
        for p in note.partials:
            if param in STORAGE_PARAMS:
                value = getattr(p, param)
            elif param == "f_coef":
                derived = _f_coef_from_partial(p, f0=note.f0_hz, B=note.B)
                if derived is None:
                    continue
                value = derived
            else:
                continue
            points.append(CurvePoint(
                k=p.k,
                value=float(value),
                sigma=p.sigma,
                fit_quality=p.fit_quality,
                origin=p.origin,
            ))
        result[param] = points
    return CurvesPayload(midi=note.midi, velocity=note.vel, parameters=result)
