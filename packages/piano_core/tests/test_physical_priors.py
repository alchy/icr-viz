"""Tests for physical priors (NLS inharmonicity + WLS power laws)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.completion.physical_priors import (
    amplitude_envelope_predict,
    fit_inharmonicity_nls,
    power_law_predict,
    wls_amplitude_envelope,
    wls_log_log,
)
from piano_core.models.partial import Partial


# ---- synthetic partial generators ----------------------------------------

def _ideal_partial(k: int, f0: float, B: float, *, A0: float = 1.0, tau1: float = 1.0) -> Partial:
    """Generate a partial whose frequency follows the exact inharmonicity model."""
    f_hz = k * f0 * math.sqrt(1.0 + B * k * k)
    return Partial(
        k=k, f_hz=f_hz, A0=A0, tau1=tau1, tau2=tau1, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=1.0,
    )


# ---- inharmonicity NLS ---------------------------------------------------

def test_inharmonicity_fit_recovers_known_B():
    f0 = 261.6
    B_true = 5e-4
    partials = [_ideal_partial(k, f0, B_true) for k in range(1, 31)]
    fit = fit_inharmonicity_nls(partials, f0=f0)
    assert fit.B_hat == pytest.approx(B_true, rel=1e-4)
    assert fit.B_var >= 0
    assert fit.n_used == 30
    assert not fit.warnings


def test_inharmonicity_zero_B_produces_zero_estimate():
    """When partials follow perfect harmonic series (B=0), fit should return B ≈ 0."""
    f0 = 440.0
    partials = [_ideal_partial(k, f0, 0.0) for k in range(1, 20)]
    fit = fit_inharmonicity_nls(partials, f0=f0)
    assert abs(fit.B_hat) < 1e-10
    # And the "negligible signal" warning fires
    assert any("negligible" in w for w in fit.warnings)


def test_inharmonicity_handles_f0_zero_gracefully():
    partials = [_ideal_partial(k, 100.0, 1e-4) for k in range(1, 10)]
    fit = fit_inharmonicity_nls(partials, f0=0.0)
    assert fit.B_hat == 0.0
    assert any("f0" in w for w in fit.warnings)


def test_inharmonicity_empty_partials():
    fit = fit_inharmonicity_nls([], f0=440.0)
    assert fit.n_used == 0
    assert fit.B_hat == 0.0


def test_inharmonicity_noisy_data_converges_close():
    f0 = 146.8
    B_true = 2e-3
    rng = np.random.default_rng(42)
    partials = []
    for k in range(1, 41):
        f_ideal = k * f0 * math.sqrt(1.0 + B_true * k * k)
        # ±0.5% noise
        f_noisy = f_ideal * (1.0 + rng.normal(0.0, 0.005))
        partials.append(Partial(
            k=k, f_hz=f_noisy, A0=1.0, tau1=1.0, tau2=1.0, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=1.0,
        ))
    fit = fit_inharmonicity_nls(partials, f0=f0)
    # Within 10% relative error under this noise level
    assert fit.B_hat == pytest.approx(B_true, rel=0.10)


# ---- power law WLS -------------------------------------------------------

def test_wls_log_log_recovers_pure_power_law():
    """If y = C * k^(-alpha), fit should return (log C, alpha)."""
    alpha_true = 0.7
    C = 5.0
    k = np.arange(1, 31)
    y = C * k.astype(float) ** (-alpha_true)
    fit = wls_log_log(k, y)
    c_hat, alpha_hat = fit.phi
    assert c_hat == pytest.approx(math.log(C), abs=1e-10)
    assert alpha_hat == pytest.approx(alpha_true, abs=1e-10)
    assert fit.n_used == 30


def test_wls_log_log_skips_non_positive_samples():
    k = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([1.0, 0.5, -1.0, 0.25, 0.0])   # includes negative and zero
    fit = wls_log_log(k, y)
    assert fit.n_used == 3
    assert any("non-positive" in w for w in fit.warnings)


def test_wls_log_log_needs_two_points_minimum():
    fit = wls_log_log([1.0], [1.0])
    assert fit.n_used == 1
    assert any("fewer than 2" in w for w in fit.warnings)


def test_power_law_predict_inverts_fit():
    alpha_true = 1.5
    k = np.arange(1, 11)
    y = 2.0 * k.astype(float) ** (-alpha_true)
    fit = wls_log_log(k, y)
    yhat = power_law_predict(fit, k)
    assert np.allclose(yhat, y, rtol=1e-6)


def test_wls_log_log_weights_bias_fit():
    alpha_true = 0.5
    C = 1.0
    k = np.arange(1, 11, dtype=float)
    y = C * k ** (-alpha_true)
    # Inject an outlier at k=5 and down-weight it to near zero
    y[4] = 100.0
    w = np.ones_like(y)
    w[4] = 0.0
    fit = wls_log_log(k, y, weights=w)
    _, alpha_hat = fit.phi
    assert alpha_hat == pytest.approx(alpha_true, abs=1e-8)


# ---- amplitude envelope WLS ----------------------------------------------

def test_amplitude_envelope_recovers_beta_and_mu():
    c_true = 2.0
    beta_true = 0.5
    mu_true = 0.1
    partials = []
    for k in range(1, 21):
        a = math.exp(c_true - beta_true * math.log(k) - mu_true * k)
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=a, tau1=1.0, tau2=1.0, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=1.0,
        ))
    fit = wls_amplitude_envelope(partials)
    c_hat, beta_hat, mu_hat = fit.theta
    assert c_hat == pytest.approx(c_true, abs=1e-8)
    assert beta_hat == pytest.approx(beta_true, abs=1e-8)
    assert mu_hat == pytest.approx(mu_true, abs=1e-8)


def test_amplitude_envelope_predict_roundtrip():
    c_true, beta_true, mu_true = 1.0, 0.3, 0.05
    partials = []
    for k in range(1, 16):
        a = math.exp(c_true - beta_true * math.log(k) - mu_true * k)
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=a, tau1=1.0, tau2=1.0, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=1.0,
        ))
    fit = wls_amplitude_envelope(partials)
    for k in [1, 5, 10, 15]:
        predicted = amplitude_envelope_predict(fit, k)
        expected = math.exp(c_true - beta_true * math.log(k) - mu_true * k)
        assert predicted == pytest.approx(expected, rel=1e-8)


def test_amplitude_envelope_too_few_partials():
    partials = [
        Partial(k=1, f_hz=100.0, A0=1.0, tau1=1.0, tau2=1.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=2, f_hz=200.0, A0=0.5, tau1=1.0, tau2=1.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
    ]
    fit = wls_amplitude_envelope(partials)
    assert fit.n_used == 2
    assert any("fewer than 3" in w for w in fit.warnings)


def test_amplitude_envelope_skips_nonpositive_A0():
    partials = [
        Partial(k=1, f_hz=100.0, A0=1.0, tau1=1.0, tau2=1.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=2, f_hz=200.0, A0=0.0, tau1=1.0, tau2=1.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=3, f_hz=300.0, A0=-0.1, tau1=1.0, tau2=1.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=4, f_hz=400.0, A0=0.3, tau1=1.0, tau2=1.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
    ]
    fit = wls_amplitude_envelope(partials)
    assert fit.n_used == 2    # only k=1 and k=4 usable
    assert any("fewer than 3" in w for w in fit.warnings)


def test_amplitude_envelope_weighted_by_fit_quality():
    """Setting low fit_quality on an outlier should reduce its influence on beta/mu."""
    c_true, beta_true, mu_true = 1.5, 0.4, 0.08
    partials = []
    for k in range(1, 21):
        a_true = math.exp(c_true - beta_true * math.log(k) - mu_true * k)
        if k == 10:
            a = a_true * 10  # huge outlier at k=10
            fq = 0.0         # weight to zero
        else:
            a = a_true
            fq = 1.0
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=a, tau1=1.0, tau2=1.0, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=fq,
        ))
    fit = wls_amplitude_envelope(partials, weight_by_fit_quality=True)
    # With outlier down-weighted, recovery should be tight
    c_hat, beta_hat, mu_hat = fit.theta
    assert beta_hat == pytest.approx(beta_true, abs=1e-4)
    assert mu_hat == pytest.approx(mu_true, abs=1e-4)
