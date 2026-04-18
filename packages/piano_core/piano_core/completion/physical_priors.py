"""Physical priors — NLS / WLS fits for piano-acoustic partial parameters.

Three parametric models from `math_partial_completion.md §3`:

  1. Inharmonicity (math §3.1):  f_coef(k) = sqrt(1 + B * k**2)
     where f_coef is the dimensionless residual f_hz / (k * f0) after
     fundamental-frequency normalization. Fit B via non-linear least squares.

  2. Decay-time power law (math §3.2):  log tau(k) = c - alpha * log(k)
     Linearises into a weighted LS problem. Produces (c, alpha) estimates
     plus their covariance.

  3. Amplitude envelope (math §3.4):  log A0(k) = c - beta * log(k) - mu * k
     Multi-variable WLS. Captures both the power-law decay of spectral
     amplitudes and an exponential roll-off for high partials.

All three functions are pure — they take Partial sequences (or raw arrays),
return structured results, and never log-truncate silently: edge cases from
math §11 emit explicit warnings in diagnostics (not module-level `logging`
warnings) so callers can aggregate them into ApplyDiagnostics.

Deterministic: none of these use randomness. They take no `random_seed`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from piano_core.models.partial import Partial


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InharmonicityFit:
    """Output of fit_inharmonicity_nls.

    - `B_hat` : best-fit inharmonicity coefficient (typically 1e-5..1e-2 for piano)
    - `B_var` : estimated variance of B_hat
    - `n_used` : how many partials fed into the fit
    - `warnings` : tuple of text warnings from math §11 guards
    """

    B_hat: float
    B_var: float
    n_used: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PowerLawFit:
    """Weighted LS fit of log y = c - alpha * log k.

    `phi` = [c_hat, alpha_hat]
    `cov` = 2x2 covariance matrix of phi
    """

    phi: np.ndarray                          # shape (2,)
    cov: np.ndarray                          # shape (2, 2)
    n_used: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AmplitudeEnvelopeFit:
    """WLS fit of log A0 = c - beta * log k - mu * k.

    `theta` = [c_hat, beta_hat, mu_hat]
    `cov` = 3x3 covariance matrix of theta
    """

    theta: np.ndarray                        # shape (3,)
    cov: np.ndarray                          # shape (3, 3)
    n_used: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# 1. Inharmonicity via NLS on f_coef(k) = sqrt(1 + B*k^2)
# ---------------------------------------------------------------------------

def fit_inharmonicity_nls(
    partials: Sequence[Partial],
    *,
    f0: float,
) -> InharmonicityFit:
    """Estimate B from partial frequencies.

    Math:
        f_coef(k) = f_hz(k) / (k * f0) = sqrt(1 + B * k^2)   (math §3.1)
        => f_coef^2 = 1 + B * k^2
        => (f_coef^2 - 1) / k^2 = B    (simple closed form when perfect)

    We use the closed form above to get a starting estimate, then refine via
    weighted linear LS: y = B * x where y = f_coef^2 - 1 and x = k^2 weighted
    by fit_quality (since high-quality partials anchor the fit).

    Edge case (math §11.3): when the sample variance of (f_coef - 1) < 1e-6
    there is no inharmonicity signal to fit — return (B=0, var=1e-12) and a
    warning. This keeps pipelines that blindly use B_hat from blowing up.
    """
    warnings: list[str] = []

    if f0 <= 0:
        warnings.append("fit_inharmonicity: f0 <= 0, returning B=0")
        return InharmonicityFit(B_hat=0.0, B_var=1e-12, n_used=0, warnings=tuple(warnings))

    # Build weighted design for linear fit y = B * x.
    xs: list[float] = []
    ys: list[float] = []
    ws: list[float] = []
    for p in partials:
        if p.k < 1 or p.f_hz <= 0:
            continue
        f_coef = p.f_hz / (p.k * f0)
        y_i = f_coef * f_coef - 1.0
        x_i = float(p.k * p.k)
        # fit_quality as weight — high-quality partials dominate the fit.
        w_i = max(0.0, float(p.fit_quality))
        xs.append(x_i)
        ys.append(y_i)
        ws.append(w_i)

    if not xs:
        warnings.append("fit_inharmonicity: no usable partials, returning B=0")
        return InharmonicityFit(B_hat=0.0, B_var=1e-12, n_used=0, warnings=tuple(warnings))

    x = np.array(xs)
    y = np.array(ys)
    w = np.array(ws)
    n = x.size

    # math §11.3 guard: var(f_coef - 1) < 1e-6 => no signal.
    f_coef_minus_one = np.sqrt(y + 1.0) - 1.0
    if float(np.var(f_coef_minus_one)) < 1e-6:
        warnings.append("fit_inharmonicity: negligible inharmonicity signal (var<1e-6), B=0")
        return InharmonicityFit(B_hat=0.0, B_var=1e-12, n_used=n, warnings=tuple(warnings))

    # Weighted LS through origin: minimize sum(w_i * (y_i - B*x_i)^2)
    # dL/dB = -2 sum(w_i x_i (y_i - B x_i)) = 0
    # => B = sum(w x y) / sum(w x^2)
    sum_wxx = float(np.sum(w * x * x))
    if sum_wxx <= 1e-20:
        warnings.append("fit_inharmonicity: zero weighted design matrix, B=0")
        return InharmonicityFit(B_hat=0.0, B_var=1e-12, n_used=n, warnings=tuple(warnings))

    B_hat = float(np.sum(w * x * y) / sum_wxx)

    # Residual-based variance estimate: sigma^2 = RSS / (n - 1), var(B) = sigma^2 / sum(w x^2)
    rss = float(np.sum(w * (y - B_hat * x) ** 2))
    sigma2 = rss / max(n - 1, 1)
    B_var = max(sigma2 / sum_wxx, 1e-30)

    # B should be non-negative physically; if negative, clamp with a warning.
    if B_hat < 0:
        warnings.append(f"fit_inharmonicity: negative B={B_hat:.3e} clamped to 0")
        B_hat = 0.0

    logger.debug(
        "fit.inharmonicity",
        extra={"B_hat": B_hat, "B_var": B_var, "n_used": n, "f0": f0},
    )
    return InharmonicityFit(B_hat=B_hat, B_var=B_var, n_used=n, warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# 2. Power law:  log y = c - alpha * log k
# ---------------------------------------------------------------------------

def wls_log_log(
    k: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
    *,
    weights: Sequence[float] | np.ndarray | None = None,
) -> PowerLawFit:
    """Weighted LS fit of log y = c - alpha * log k.

    Used for tau1(k), tau2(k), and any observable where a power-law of k is
    the expected form. Returns (phi=[c, alpha], cov=2x2).

    Convention: alpha is the **positive** slope of a **decreasing** power law.
    Internally the fit is over the unconstrained design [1, -log k] so alpha
    falls out positive when y decreases with k.
    """
    warnings: list[str] = []
    k_arr = np.asarray(k, dtype=float)
    y_arr = np.asarray(y, dtype=float)

    # Filter non-positive (log undefined)
    valid = (k_arr > 0) & (y_arr > 0)
    if not np.all(valid):
        dropped = int((~valid).sum())
        warnings.append(f"wls_log_log: dropped {dropped} non-positive samples")
    k_arr = k_arr[valid]
    y_arr = y_arr[valid]
    n = k_arr.size

    if n < 2:
        warnings.append("wls_log_log: fewer than 2 usable samples")
        return PowerLawFit(
            phi=np.array([0.0, 0.0]),
            cov=np.full((2, 2), np.nan),
            n_used=n,
            warnings=tuple(warnings),
        )

    if weights is None:
        w = np.ones_like(y_arr)
    else:
        w = np.asarray(weights, dtype=float)[valid]
        if w.size != n:
            raise ValueError("weights must align with k/y")
        if np.any(w < 0) or not np.all(np.isfinite(w)):
            raise ValueError("weights must be non-negative and finite")

    # Design matrix with columns [1, -log k]
    log_k = np.log(k_arr)
    log_y = np.log(y_arr)
    X = np.column_stack([np.ones_like(log_k), -log_k])
    W = np.diag(w)

    try:
        XtWX = X.T @ W @ X
        XtWy = X.T @ W @ log_y
        phi = np.linalg.solve(XtWX, XtWy)
    except np.linalg.LinAlgError:
        phi, *_ = np.linalg.lstsq(X, log_y, rcond=None)
        warnings.append("wls_log_log: singular weighted design, fell back to OLS")

    residual = log_y - X @ phi
    dof = max(n - 2, 1)
    sigma2 = float((w * residual * residual).sum() / dof)
    try:
        cov = sigma2 * np.linalg.inv(XtWX)
    except np.linalg.LinAlgError:
        cov = np.full((2, 2), np.nan)

    logger.debug(
        "fit.wls_log_log",
        extra={"c": float(phi[0]), "alpha": float(phi[1]), "n_used": n},
    )
    return PowerLawFit(
        phi=phi,
        cov=cov,
        n_used=n,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# 3. Amplitude envelope:  log A0 = c - beta * log k - mu * k
# ---------------------------------------------------------------------------

def wls_amplitude_envelope(
    partials: Sequence[Partial],
    *,
    weight_by_fit_quality: bool = True,
) -> AmplitudeEnvelopeFit:
    """Fit log A0(k) = c - beta * log k - mu * k (math §3.4).

    Multi-variable weighted LS over columns [1, -log k, -k]. Returns the
    3-vector theta = [c, beta, mu] and its 3x3 covariance.
    """
    warnings: list[str] = []

    valid = [p for p in partials if p.k >= 1 and p.A0 > 0]
    if len(valid) < 3:
        warnings.append("wls_amplitude_envelope: fewer than 3 usable partials")
        return AmplitudeEnvelopeFit(
            theta=np.zeros(3),
            cov=np.full((3, 3), np.nan),
            n_used=len(valid),
            warnings=tuple(warnings),
        )

    k_arr = np.array([p.k for p in valid], dtype=float)
    a_arr = np.array([p.A0 for p in valid], dtype=float)
    log_k = np.log(k_arr)
    log_a = np.log(a_arr)
    X = np.column_stack([np.ones_like(log_k), -log_k, -k_arr])

    if weight_by_fit_quality:
        w = np.array([max(0.0, float(p.fit_quality)) for p in valid])
        # Guard against all-zero weights
        if float(np.sum(w)) < 1e-12:
            w = np.ones_like(w)
            warnings.append("wls_amplitude_envelope: all fit_quality weights zero, using uniform")
    else:
        w = np.ones_like(log_k)

    W = np.diag(w)
    try:
        XtWX = X.T @ W @ X
        XtWy = X.T @ W @ log_a
        theta = np.linalg.solve(XtWX, XtWy)
    except np.linalg.LinAlgError:
        theta, *_ = np.linalg.lstsq(X, log_a, rcond=None)
        warnings.append("wls_amplitude_envelope: singular design, fell back to OLS")

    residual = log_a - X @ theta
    dof = max(len(valid) - 3, 1)
    sigma2 = float((w * residual * residual).sum() / dof)
    try:
        cov = sigma2 * np.linalg.inv(XtWX)
    except np.linalg.LinAlgError:
        cov = np.full((3, 3), np.nan)

    logger.debug(
        "fit.amplitude_envelope",
        extra={
            "c": float(theta[0]),
            "beta": float(theta[1]),
            "mu": float(theta[2]),
            "n_used": len(valid),
        },
    )
    return AmplitudeEnvelopeFit(
        theta=theta,
        cov=cov,
        n_used=len(valid),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Convenience: evaluate fitted priors at arbitrary k
# ---------------------------------------------------------------------------

def power_law_predict(fit: PowerLawFit, k: np.ndarray | float) -> np.ndarray | float:
    """Evaluate y = exp(c - alpha * log k) at new k values."""
    k_arr = np.asarray(k, dtype=float)
    c, alpha = float(fit.phi[0]), float(fit.phi[1])
    out = np.exp(c - alpha * np.log(np.maximum(k_arr, 1e-15)))
    if np.isscalar(k) or k_arr.ndim == 0:
        return float(out)
    return out


def amplitude_envelope_predict(fit: AmplitudeEnvelopeFit, k: np.ndarray | float) -> np.ndarray | float:
    k_arr = np.asarray(k, dtype=float)
    c, beta, mu = float(fit.theta[0]), float(fit.theta[1]), float(fit.theta[2])
    out = np.exp(c - beta * np.log(np.maximum(k_arr, 1e-15)) - mu * k_arr)
    if np.isscalar(k) or k_arr.ndim == 0:
        return float(out)
    return out
