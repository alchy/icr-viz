"""Per-note physical-parameter fit results.

Wraps the existing physical_priors fits (inharmonicity NLS, tau power-law WLS,
A0 envelope WLS) into a single `fit_note()` call that returns a compact,
JSON-serialisable `NoteMathDiag`. The math-analysis service calls this once
per (midi, velocity) pair.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from piano_core.completion.physical_priors import (
    amplitude_envelope_predict,
    fit_inharmonicity_nls,
    power_law_predict,
    wls_amplitude_envelope,
    wls_log_log,
)
from piano_core.models.note import Note


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NoteMathDiag:
    """Compact per-note physical-fit summary — one row in MathAnalysisReport.per_note.

    Everything is optional (`None`) when the fit failed or not enough data —
    callers inspect presence before drawing.
    """

    midi: int
    velocity: int
    f0_hz: float
    B_hat: float | None = None
    B_sigma: float | None = None
    tau1_alpha: float | None = None
    tau1_alpha_sigma: float | None = None
    tau2_alpha: float | None = None
    tau2_alpha_sigma: float | None = None
    A0_beta: float | None = None
    A0_mu: float | None = None
    gamma: float | None = None                   # median tau2/tau1
    fit_rmse: dict[str, float] = field(default_factory=dict)
    physical_prior_fit_quality: float = 0.0      # [0..1]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def fit_note(note: Note) -> NoteMathDiag:
    """Run all four physical-prior fits on a note and bundle the diagnostics."""
    warnings: list[str] = []

    # 1. Inharmonicity
    B_hat: float | None = None
    B_sigma: float | None = None
    if note.f0_hz > 0 and note.partials:
        fit_B = fit_inharmonicity_nls(list(note.partials), f0=note.f0_hz)
        B_hat = fit_B.B_hat
        B_sigma = math.sqrt(fit_B.B_var) if fit_B.B_var > 0 else 0.0
        warnings.extend(fit_B.warnings)

    # 2. tau1 / tau2 power laws
    tau1_alpha, tau1_alpha_sigma, tau1_rmse = _fit_power_law(note, attr="tau1")
    tau2_alpha, tau2_alpha_sigma, tau2_rmse = _fit_power_law(note, attr="tau2")

    # 3. A0 envelope
    A0_beta: float | None = None
    A0_mu: float | None = None
    A0_rmse: float | None = None
    if note.partials:
        env = wls_amplitude_envelope(list(note.partials))
        warnings.extend(env.warnings)
        if env.n_used >= 3 and np.all(np.isfinite(env.theta)):
            A0_beta = float(env.theta[1])
            A0_mu = float(env.theta[2])
            # RMSE in log-A0 space
            residuals = []
            for p in note.partials:
                if p.k >= 1 and p.A0 > 0:
                    predicted = amplitude_envelope_predict(env, float(p.k))
                    residuals.append(math.log(predicted) - math.log(p.A0))
            if residuals:
                A0_rmse = float(np.sqrt(np.mean(np.square(residuals))))

    # 4. gamma = tau2 / tau1 — robust median across partials
    gamma = _gamma_median(note)

    fit_rmse: dict[str, float] = {}
    if tau1_rmse is not None:
        fit_rmse["tau1"] = tau1_rmse
    if tau2_rmse is not None:
        fit_rmse["tau2"] = tau2_rmse
    if A0_rmse is not None:
        fit_rmse["A0"] = A0_rmse

    quality = _quality_score(
        n_partials=len(note.partials),
        fit_rmse=fit_rmse,
        has_B=B_hat is not None,
    )

    logger.debug(
        "physical_fit.note",
        extra={
            "midi": note.midi, "velocity": note.vel,
            "B_hat": B_hat, "tau1_alpha": tau1_alpha,
            "A0_beta": A0_beta, "gamma": gamma,
            "quality": quality,
        },
    )

    return NoteMathDiag(
        midi=note.midi,
        velocity=note.vel,
        f0_hz=note.f0_hz,
        B_hat=B_hat,
        B_sigma=B_sigma,
        tau1_alpha=tau1_alpha,
        tau1_alpha_sigma=tau1_alpha_sigma,
        tau2_alpha=tau2_alpha,
        tau2_alpha_sigma=tau2_alpha_sigma,
        A0_beta=A0_beta,
        A0_mu=A0_mu,
        gamma=gamma,
        fit_rmse=fit_rmse,
        physical_prior_fit_quality=quality,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fit_power_law(note: Note, *, attr: str) -> tuple[float | None, float | None, float | None]:
    if not note.partials:
        return None, None, None
    ks = [p.k for p in note.partials if p.k >= 1]
    ys = [float(getattr(p, attr)) for p in note.partials if p.k >= 1]
    weights = [max(0.01, min(float(p.fit_quality), 1.0)) for p in note.partials if p.k >= 1]
    if len(ks) < 3:
        return None, None, None
    fit = wls_log_log(ks, ys, weights=weights)
    if fit.n_used < 3 or not np.all(np.isfinite(fit.phi)):
        return None, None, None
    alpha = float(fit.phi[1])
    alpha_sigma: float | None = None
    if np.all(np.isfinite(fit.cov)):
        var = float(fit.cov[1, 1])
        alpha_sigma = math.sqrt(var) if var >= 0 else None

    # RMSE in log-y space
    residuals = []
    for k, y, w in zip(ks, ys, weights):
        if y > 0:
            predicted = power_law_predict(fit, float(k))
            residuals.append(math.log(predicted) - math.log(y))
    rmse = float(np.sqrt(np.mean(np.square(residuals)))) if residuals else None
    return alpha, alpha_sigma, rmse


def _gamma_median(note: Note) -> float | None:
    """Median of tau2/tau1 over partials with usable values."""
    ratios = []
    for p in note.partials:
        if p.tau1 > 1e-9 and p.tau2 > 0:
            ratios.append(p.tau2 / p.tau1)
    if not ratios:
        return None
    return float(np.median(ratios))


def _quality_score(*, n_partials: int, fit_rmse: dict[str, float], has_B: bool) -> float:
    """Combine data volume + fit precision into a bounded [0, 1] score.

    High-volume notes with low log-space RMSE score highest. Notes with missing
    fits score proportionally less.
    """
    if n_partials == 0:
        return 0.0
    volume = min(n_partials / 20.0, 1.0)
    if not fit_rmse:
        precision = 0.5 if has_B else 0.0
    else:
        # Typical log-RMSE sits around 0.1-0.3 on clean notes; score 1.0 at RMSE=0.
        avg = float(np.mean(list(fit_rmse.values())))
        precision = max(0.0, 1.0 - min(avg, 1.0))
    return round(0.5 * volume + 0.5 * precision, 4)
