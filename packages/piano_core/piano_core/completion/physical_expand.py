"""Physical-prior expansion of a partial set (math §12.1).

Given an incomplete set of measured partials for a single note, extrapolate
physically-plausible values up to a target k_max using the fitted priors:

  - Frequency via the inharmonicity model: f_hz(k) = k * f0 * sqrt(1 + B*k^2)
  - tau1(k), tau2(k) via power-law fit (wls_log_log)
  - A0(k) via amplitude envelope fit (wls_amplitude_envelope)
  - a1 held at the average of observed a1 (or 1.0 fallback — no physics prior)
  - beat_hz held at zero for extrapolated partials (no reliable prior)
  - phi uniformly random in [0, 2pi) seeded by random_seed (phase is not
    a physically meaningful extrapolation — only amplitudes/decays are)

Extrapolated partials are stamped `origin="extrapolated"` and assigned a
sentinel `fit_quality=0.5` so downstream consumers can visually distinguish
them from measured observations.

`physical_expand` is a pure function; it never mutates the input sequence.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from piano_core.completion.physical_priors import (
    amplitude_envelope_predict,
    fit_inharmonicity_nls,
    power_law_predict,
    wls_amplitude_envelope,
    wls_log_log,
)
from piano_core.models.partial import Partial
from piano_core.splines.bounds import clamp_to_bounds


logger = logging.getLogger(__name__)


# Extrapolated partials carry this sentinel quality so visualizations can
# color them differently from measured observations (legacy behaviour —
# `Partial.fit_quality==0.5` is the extrapolation marker per i1 F-3).
EXTRAPOLATION_FIT_QUALITY = 0.5


@dataclass(frozen=True)
class PhysicalExpandDiagnostics:
    n_observed: int
    n_extrapolated: int
    k_max: int
    B_hat: float
    B_var: float
    warnings: tuple[str, ...] = field(default_factory=tuple)


def physical_expand(
    partials: Sequence[Partial],
    *,
    f0: float,
    B_override: float | None = None,
    k_max: int,
    random_seed: int = 0,
) -> tuple[list[Partial], PhysicalExpandDiagnostics]:
    """Fill gaps from 1..k_max using physical priors fitted from `partials`.

    Parameters
    ----------
    partials : measured partials for a single note. Must all share the same
        (midi, vel) — this function does not verify that.
    f0 : the note's fundamental frequency (Hz). Used directly for frequency
        extrapolation and passed into the inharmonicity NLS fit.
    B_override : if supplied, skip the NLS fit and use this B directly.
    k_max : target partial count (inclusive). All k in 1..k_max appear in the
        output; any gaps are filled via priors.
    random_seed : seeds the RNG used for phase generation on extrapolated partials.

    Returns
    -------
    (expanded_partials, diagnostics) — sorted by k.
    """
    warnings: list[str] = []

    if k_max < 1:
        raise ValueError(f"k_max must be >= 1, got {k_max}")
    if f0 <= 0:
        raise ValueError(f"f0 must be positive, got {f0}")

    observed = sorted((p for p in partials if 1 <= p.k <= k_max), key=lambda p: p.k)
    observed_by_k: dict[int, Partial] = {p.k: p for p in observed}

    # --- 1. Inharmonicity --------------------------------------------------
    if B_override is not None:
        B_hat = float(B_override)
        B_var = 0.0
    else:
        fit_B = fit_inharmonicity_nls(observed, f0=f0)
        B_hat = fit_B.B_hat
        B_var = fit_B.B_var
        warnings.extend(fit_B.warnings)

    # --- 2. Fit tau1, tau2 priors (log-log power laws) --------------------
    tau1_fit = _fit_tau_prior(observed, attr="tau1")
    tau2_fit = _fit_tau_prior(observed, attr="tau2")
    warnings.extend(tau1_fit.warnings)
    warnings.extend(tau2_fit.warnings)

    # --- 3. Fit A0 envelope -----------------------------------------------
    envelope_fit = wls_amplitude_envelope(observed)
    warnings.extend(envelope_fit.warnings)

    # --- 4. Scalar fallbacks for a1, beat_hz, phi ------------------------
    a1_prior = _a1_prior(observed)
    rng = np.random.default_rng(random_seed)

    # --- 5. Assemble full 1..k_max ---------------------------------------
    expanded: list[Partial] = []
    n_extrapolated = 0

    for k in range(1, k_max + 1):
        if k in observed_by_k:
            expanded.append(observed_by_k[k])
            continue

        # Extrapolated partial
        f_hz = k * f0 * math.sqrt(1.0 + B_hat * k * k) if B_hat >= 0 else k * f0
        tau1_pred = float(power_law_predict(tau1_fit, float(k)))
        tau2_pred = float(power_law_predict(tau2_fit, float(k)))
        A0_pred = float(amplitude_envelope_predict(envelope_fit, float(k)))

        # Clamp into physical bounds — priors can over/underflow for k far
        # outside observation range.
        tau1_pred = float(clamp_to_bounds(tau1_pred, "tau1"))
        tau2_pred = float(clamp_to_bounds(tau2_pred, "tau2"))
        A0_pred = float(clamp_to_bounds(A0_pred, "A0"))

        expanded.append(Partial(
            k=k,
            f_hz=f_hz,
            A0=A0_pred,
            tau1=tau1_pred,
            tau2=tau2_pred,
            a1=a1_prior,
            beat_hz=0.0,
            phi=float(rng.uniform(0.0, 2.0 * math.pi)),
            fit_quality=EXTRAPOLATION_FIT_QUALITY,
            sigma=None,
            origin="extrapolated",
        ))
        n_extrapolated += 1

    logger.info(
        "physical_expand",
        extra={
            "n_observed": len(observed),
            "n_extrapolated": n_extrapolated,
            "k_max": k_max,
            "B_hat": B_hat,
            "f0": f0,
            "seed": random_seed,
        },
    )

    return expanded, PhysicalExpandDiagnostics(
        n_observed=len(observed),
        n_extrapolated=n_extrapolated,
        k_max=k_max,
        B_hat=B_hat,
        B_var=B_var,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------

def _fit_tau_prior(observed: Sequence[Partial], *, attr: str):
    """Fit power-law prior for tau1 or tau2 over observed partials."""
    if not observed:
        # Degenerate — empty fit so predictor returns exp(0) = 1.
        return wls_log_log([1.0], [1.0])
    ks = [p.k for p in observed if p.k >= 1]
    ys = [float(getattr(p, attr)) for p in observed if p.k >= 1]
    weights = [max(0.01, min(float(p.fit_quality), 1.0)) for p in observed if p.k >= 1]
    return wls_log_log(ks, ys, weights=weights)


def _a1_prior(observed: Sequence[Partial]) -> float:
    """Median a1 across observed partials, or 1.0 if none."""
    if not observed:
        return 1.0
    vals = np.array([p.a1 for p in observed], dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return 1.0
    return float(np.clip(np.median(vals), 0.0, 1.0))
