import { Sample, Harmonic, Partial, AdditiveNoteParams, AnalysisResult, PhysicalParams, SynthesisMode, PHYSICAL_PARAM_KEYS, ADDITIVE_NOTE_REQUIRED_KEYS, ADDITIVE_NOTE_OPTIONAL_KEYS, PARTIAL_EXPECTED_KEYS, CorrectionConfig, AnchorRef, DetectionMethod } from '../types';
import _ from 'lodash';

// ============================================================
// DATA PARSING - matches real GitHub repo format
// ============================================================

/**
 * Parse raw JSON from GitHub soundbank into Sample[].
 * Physical: { notes: { m021: { midi, f0_hz, B, ... } } }
 * Additive: { notes: { m021_vel0: { midi, vel, partials: [...] } } }
 */
export function parseSoundbankJSON(
  data: any,
  bankId: string,
  bankName: string,
  mode: SynthesisMode
): { samples: Sample[], metadata: Record<string, any>, debug: string } {
  const metadata = data.metadata || {};
  const notes = data.notes || {};
  const noteKeys = Object.keys(notes);
  const samples: Sample[] = [];
  const debugLines: string[] = [];

  debugLines.push(`Parsing ${bankName} (${mode}): ${noteKeys.length} note keys found`);

  if (noteKeys.length === 0) {
    // Try alternative data paths
    if (data.data && typeof data.data === 'object') {
      debugLines.push(`Fallback: trying data.data path...`);
      return parseSoundbankJSON({ notes: data.data, metadata }, bankId, bankName, mode);
    }
    debugLines.push(`ERROR: No notes found in bank`);
    return { samples, metadata, debug: debugLines.join('\n') };
  }

  debugLines.push(`Keys sample: ${noteKeys.slice(0, 5).join(', ')}${noteKeys.length > 5 ? '...' : ''}`);

  // Key validation: check first note against expected schema
  const firstNote = notes[noteKeys[0]];
  if (firstNote && typeof firstNote === 'object') {
    const actualKeys = new Set(Object.keys(firstNote));

    if (mode === 'physical') {
      const expectedKeys = new Set([...PHYSICAL_PARAM_KEYS, 'midi']);
      const missing = [...expectedKeys].filter(k => !actualKeys.has(k));
      const extra = [...actualKeys].filter(k => !expectedKeys.has(k));
      if (missing.length > 0) {
        debugLines.push(`KEY WARNING: missing physical keys: ${missing.join(', ')}`);
      }
      if (extra.length > 0) {
        debugLines.push(`KEY INFO: extra keys ignored: ${extra.join(', ')}`);
      }
      if (missing.length === 0 && extra.length === 0) {
        debugLines.push(`KEY OK: all ${expectedKeys.size} physical keys present`);
      }
    } else {
      const requiredKeys = new Set<string>(ADDITIVE_NOTE_REQUIRED_KEYS);
      const knownOptional = new Set<string>(ADDITIVE_NOTE_OPTIONAL_KEYS);
      const allExpected = new Set([...requiredKeys, ...knownOptional, 'midi', 'vel']);

      const missingRequired = [...requiredKeys].filter(k => !actualKeys.has(k));
      const missingOptional = [...knownOptional].filter(k => !actualKeys.has(k));
      const unknown = [...actualKeys].filter(k => !allExpected.has(k) && k !== 'partials');

      if (missingRequired.length > 0) {
        debugLines.push(`KEY ERROR: missing required additive keys: ${missingRequired.join(', ')} — bank may not load correctly`);
      }
      if (missingOptional.length > 0) {
        debugLines.push(`KEY INFO: missing optional keys (defaults used): ${missingOptional.join(', ')}`);
      }
      if (unknown.length > 0) {
        debugLines.push(`KEY INFO: unrecognized keys ignored: ${unknown.join(', ')}`);
      }
      if (missingRequired.length === 0) {
        debugLines.push(`KEY OK: all required additive keys present`);
      }

      // Validate partial keys from first partial of first note
      const firstPartials = firstNote.partials;
      if (Array.isArray(firstPartials) && firstPartials.length > 0) {
        const partialKeys = new Set(Object.keys(firstPartials[0]));
        const expectedPartialKeys = new Set<string>(PARTIAL_EXPECTED_KEYS);
        const missingPartial = [...expectedPartialKeys].filter(k => !partialKeys.has(k));
        const extraPartial = [...partialKeys].filter(k => !expectedPartialKeys.has(k));
        if (missingPartial.length > 0) {
          debugLines.push(`PARTIAL KEY WARNING: missing: ${missingPartial.join(', ')}`);
        }
        if (extraPartial.length > 0) {
          debugLines.push(`PARTIAL KEY INFO: extra keys: ${extraPartial.join(', ')}`);
        }
      }
    }
  }

  for (const key of noteKeys) {
    const note = notes[key];
    if (!note || typeof note !== 'object') continue;

    // Parse MIDI from note object or from key (m021 -> 21, m021_vel0 -> 21)
    const midi = typeof note.midi === 'number'
      ? note.midi
      : parseInt(key.replace(/^m0*/, '').replace(/_.*$/, ''));
    if (isNaN(midi)) continue;

    const sample: Sample = {
      midi,
      vel: note.vel,
      bankId,
      bankName,
    };

    if (mode === 'physical') {
      const physical: any = {};
      let paramCount = 0;
      for (const pk of PHYSICAL_PARAM_KEYS) {
        if (pk in note && typeof note[pk] === 'number') {
          physical[pk] = note[pk];
          paramCount++;
        }
      }
      if (paramCount === 0) {
        debugLines.push(`SKIP ${key}: no valid physical params (keys found: ${Object.keys(note).join(', ')})`);
        continue;
      }
      if (paramCount < PHYSICAL_PARAM_KEYS.length) {
        debugLines.push(`WARN ${key}: only ${paramCount}/${PHYSICAL_PARAM_KEYS.length} physical params found`);
      }
      sample.physical = physical as PhysicalParams;
      samples.push(sample);
    } else {
      // Additive: partials array -> convert to harmonics for analysis
      const partials = note.partials;
      if (!Array.isArray(partials) || partials.length === 0) {
        debugLines.push(`SKIP ${key}: no partials array`);
        continue;
      }
      sample.additive = {
        f0_hz: note.f0_hz || 0,
        B: note.B || 0,
        phi_diff: note.phi_diff || 0,
        attack_tau: note.attack_tau || 0,
        A_noise: note.A_noise || 0,
        noise_centroid_hz: note.noise_centroid_hz || 0,
        rms_gain: note.rms_gain || 0,
        n_strings: note.n_strings,
        rise_tau: note.rise_tau,
        stereo_width: note.stereo_width,
        partials: partials,
        eq_biquads: note.eq_biquads,
        spectral_eq: note.spectral_eq,
      };
      // Convert partials to normalized harmonics for comparison
      const maxA0 = Math.max(...partials.map((p: any) => Math.abs(p.A0 || 0)), 1e-9);
      sample.harmonics = partials.map((p: any) => ({
        index: p.k || 0,
        amplitude: (p.A0 || 0) / maxA0,
        phase: p.phi,
      }));
      samples.push(sample);
    }
  }

  const midiNums = [...new Set(samples.map(s => s.midi))].sort((a, b) => a - b);
  if (midiNums.length > 0) {
    debugLines.push(`OK: ${samples.length} samples, MIDI ${midiNums[0]}-${midiNums[midiNums.length - 1]} (${midiNums.length} unique)`);
  } else {
    debugLines.push(`WARNING: 0 samples parsed — bank keys may not match expected schema`);
  }

  if (mode === 'additive') {
    const vels = [...new Set(samples.map(s => s.vel))].filter(v => v !== undefined).sort((a, b) => (a ?? 0) - (b ?? 0));
    if (vels.length > 0) {
      debugLines.push(`Velocities: ${vels.join(', ')} (${vels.length} layers)`);
    }
  }

  return { samples, metadata, debug: debugLines.join('\n') };
}

// ============================================================
// ANCHOR SELECTION
// ============================================================

export function suggestAnchors(allSamples: Sample[], mode: SynthesisMode): AnchorRef[] {
  const relevantSamples = allSamples.filter(s =>
    mode === 'additive' ? (s.harmonics && s.harmonics.length > 0) : !!s.physical
  );
  if (relevantSamples.length === 0) return [];

  // For additive with multiple velocities, use vel=0 (or lowest) for anchors
  const filtered = mode === 'additive'
    ? relevantSamples.filter(s => (s.vel ?? 0) === 0)
    : relevantSamples;

  if (filtered.length === 0) return [];

  const groupedByMidi = _.groupBy(filtered, 'midi');
  const suggested: AnchorRef[] = [];
  const midis = Object.keys(groupedByMidi).map(Number).sort((a, b) => a - b);
  const anchorInterval = 12;

  const targetMidis = new Set<number>();
  targetMidis.add(midis[0]);
  targetMidis.add(midis[midis.length - 1]);

  for (let m = midis[0]; m <= midis[midis.length - 1]; m += anchorInterval) {
    const closest = _.minBy(midis, mid => Math.abs(mid - m));
    if (closest !== undefined) targetMidis.add(closest);
  }

  targetMidis.forEach(midi => {
    const samples = groupedByMidi[midi];
    if (!samples || samples.length === 0) return;
    if (samples.length === 1) {
      suggested.push({ midi, bankId: samples[0].bankId });
      return;
    }

    let bestBankId = samples[0].bankId;
    if (mode === 'physical') {
      const keys = PHYSICAL_PARAM_KEYS.filter(k => samples[0].physical?.[k] !== undefined);
      const avgParams: Record<string, number> = {};
      keys.forEach(k => avgParams[k] = _.mean(samples.map(s => s.physical![k])));
      bestBankId = _.minBy(samples, s => {
        let err = 0;
        keys.forEach(k => {
          const v = s.physical![k];
          const avg = avgParams[k];
          err += Math.pow(avg !== 0 ? (v - avg) / avg : v - avg, 2);
        });
        return err;
      })?.bankId || samples[0].bankId;
    } else {
      const maxH = _.max(samples.map(s => s.harmonics?.length || 0)) || 0;
      const avgH: number[] = [];
      for (let i = 0; i < maxH; i++) {
        avgH.push(_.mean(samples.map(s => s.harmonics?.[i]?.amplitude || 0)));
      }
      bestBankId = _.minBy(samples, s => {
        let err = 0;
        for (let i = 0; i < maxH; i++) {
          err += Math.pow((s.harmonics?.[i]?.amplitude || 0) - avgH[i], 2);
        }
        return err;
      })?.bankId || samples[0].bankId;
    }
    suggested.push({ midi, bankId: bestBankId });
  });

  return suggested;
}

// ============================================================
// PHYSICS-INFORMED INTERPOLATION UTILITIES
// Based on Chabassier, Chaigne & Joly (INRIA/ENSTA) piano models
// and Simionato et al. physics-informed DDSP approach.
//
// Key equations:
//   f_n = n * F0 * sqrt(1 + B * n²)    (inharmonicity)
//   σ_j = b1 + b3 * ω_j²               (frequency-dependent damping)
//   T60_n = 6*ln(10) / (2 * σ_n)
// ============================================================

// Parameters that span orders of magnitude and must be interpolated in log-space.
// Based on the physics: B ~ d⁴/(T*L²), T60 ~ exponential decay, K ~ stiffness.
const LOG_SPACE_PARAMS = new Set<keyof PhysicalParams>([
  'f0_hz', 'B', 'T60_fund', 'T60_nyq',
  'K_hardening', 'hammer_mass', 'string_mass', 'output_scale',
  'exc_x0', 'bridge_refl',
]);

// Integer/count params that should be rounded after interpolation
const INTEGER_PARAMS = new Set<keyof PhysicalParams>([
  'n_disp_stages', 'n_strings',
]);

/** Safe log: clamp to minimum to avoid -Infinity */
function safeLog(v: number): number {
  return Math.log(Math.max(Math.abs(v), 1e-15));
}

/**
 * Monotone cubic Hermite interpolation (Fritsch-Carlson method).
 * Given sorted control points (xs, ys), returns an interpolation function.
 * Guarantees C1 continuity and no overshooting between data points.
 */
function monotoneCubicSpline(xs: number[], ys: number[]): (x: number) => number {
  const n = xs.length;
  if (n === 0) return () => 0;
  if (n === 1) return () => ys[0];
  if (n === 2) {
    // Linear fallback for 2 points
    const slope = (ys[1] - ys[0]) / (xs[1] - xs[0]);
    return (x: number) => {
      if (x <= xs[0]) return ys[0];
      if (x >= xs[1]) return ys[1];
      return ys[0] + slope * (x - xs[0]);
    };
  }

  // Compute secants
  const deltas: number[] = [];
  const secants: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    deltas.push(xs[i + 1] - xs[i]);
    secants.push((ys[i + 1] - ys[i]) / deltas[i]);
  }

  // Compute tangents (Fritsch-Carlson)
  const tangents: number[] = new Array(n);
  tangents[0] = secants[0];
  tangents[n - 1] = secants[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (secants[i - 1] * secants[i] <= 0) {
      tangents[i] = 0; // Local extremum — flat tangent
    } else {
      // Harmonic mean of adjacent secants
      tangents[i] = 2 / (1 / secants[i - 1] + 1 / secants[i]);
    }
  }

  // Enforce monotonicity: clamp tangents
  for (let i = 0; i < n - 1; i++) {
    if (Math.abs(secants[i]) < 1e-30) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
    } else {
      const alpha = tangents[i] / secants[i];
      const beta = tangents[i + 1] / secants[i];
      const s = alpha * alpha + beta * beta;
      if (s > 9) {
        const tau = 3 / Math.sqrt(s);
        tangents[i] = tau * alpha * secants[i];
        tangents[i + 1] = tau * beta * secants[i];
      }
    }
  }

  return (x: number): number => {
    // Clamp to range
    if (x <= xs[0]) return ys[0];
    if (x >= xs[n - 1]) return ys[n - 1];

    // Binary search for interval
    let lo = 0, hi = n - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] <= x) lo = mid; else hi = mid;
    }

    const h = deltas[lo];
    const t = (x - xs[lo]) / h;
    const t2 = t * t;
    const t3 = t2 * t;

    // Hermite basis functions
    const h00 = 2 * t3 - 3 * t2 + 1;
    const h10 = t3 - 2 * t2 + t;
    const h01 = -2 * t3 + 3 * t2;
    const h11 = t3 - t2;

    return h00 * ys[lo] + h10 * h * tangents[lo] + h01 * ys[lo + 1] + h11 * h * tangents[lo + 1];
  };
}

/**
 * Create a spline that interpolates in log-space, then exponentiates back.
 * For parameters like B, T60, K that span orders of magnitude.
 */
function logSpaceSpline(xs: number[], ys: number[]): (x: number) => number {
  const logYs = ys.map(y => safeLog(y));
  const spline = monotoneCubicSpline(xs, logYs);
  // Preserve sign of original values
  const signs = ys.map(y => y >= 0 ? 1 : -1);
  const signSpline = monotoneCubicSpline(xs, signs);
  return (x: number) => {
    const sign = signSpline(x) >= 0 ? 1 : -1;
    return sign * Math.exp(spline(x));
  };
}

// ============================================================
// MODEL INTERPOLATION (physics-informed)
// ============================================================

export function extractRelationships(anchors: Sample[]): (midi: number) => Harmonic[] {
  if (anchors.length === 0) return () => [];
  const groupedByMidi = _.groupBy(anchors, 'midi');
  const averaged = Object.entries(groupedByMidi).map(([midiStr, samples]) => {
    const midi = parseInt(midiStr);
    const maxH = _.max(samples.map(s => s.harmonics?.length || 0)) || 0;
    const avgH: Harmonic[] = [];
    for (let i = 0; i < maxH; i++) {
      // Only average across samples that actually have this partial
      const contributing = samples.filter(s => s.harmonics && s.harmonics.length > i);
      const amplitude = contributing.length > 0
        ? _.mean(contributing.map(s => s.harmonics![i].amplitude))
        : 0;
      avgH.push({ index: i + 1, amplitude });
    }
    return { midi, harmonics: avgH };
  });
  const sorted = _.sortBy(averaged, 'midi');

  // Pre-build per-harmonic log-space splines
  const maxH = _.max(sorted.map(a => a.harmonics.length)) || 0;
  const midiValues = sorted.map(a => a.midi);
  const harmonicSplines: ((x: number) => number)[] = [];
  for (let i = 0; i < maxH; i++) {
    const amplitudes = sorted.map(a => a.harmonics[i]?.amplitude || 0);
    // Use log-space for amplitudes (perceptually relevant = dB)
    // But only if all values are positive; otherwise linear
    const allPositive = amplitudes.every(a => a > 0);
    harmonicSplines.push(
      allPositive
        ? logSpaceSpline(midiValues, amplitudes)
        : monotoneCubicSpline(midiValues, amplitudes)
    );
  }

  return (midi: number): Harmonic[] => {
    return Array.from({ length: maxH }, (_, i) => ({
      index: i + 1,
      amplitude: Math.max(0, harmonicSplines[i](midi)),
    }));
  };
}

export function extractPhysicalRelationships(anchors: Sample[]): (midi: number) => PhysicalParams {
  if (anchors.length === 0) return () => ({} as PhysicalParams);
  const groupedByMidi = _.groupBy(anchors, 'midi');
  const averaged = Object.entries(groupedByMidi).map(([midiStr, samples]) => {
    const midi = parseInt(midiStr);
    const allKeys = new Set<keyof PhysicalParams>();
    samples.forEach(s => PHYSICAL_PARAM_KEYS.forEach(k => { if (s.physical?.[k] !== undefined) allKeys.add(k); }));
    const avgP: any = {};
    allKeys.forEach(k => {
      const contributing = samples.filter(s => s.physical?.[k] !== undefined);
      avgP[k] = contributing.length > 0 ? _.mean(contributing.map(s => s.physical![k])) : 0;
    });
    return { midi, physical: avgP as PhysicalParams };
  });
  const sorted = _.sortBy(averaged, 'midi');

  // Pre-build per-parameter splines with physics-appropriate space
  const allKeys = Object.keys(sorted[0]?.physical || {}) as (keyof PhysicalParams)[];
  const midiValues = sorted.map(a => a.midi);
  const paramSplines: Record<string, (x: number) => number> = {};

  for (const k of allKeys) {
    const values = sorted.map(a => a.physical[k]);
    if (LOG_SPACE_PARAMS.has(k) && values.every(v => v > 0)) {
      // Log-space cubic spline for exponentially-varying params
      paramSplines[k] = logSpaceSpline(midiValues, values);
    } else {
      // Linear-space cubic spline for linear params (exponents, counts, etc.)
      paramSplines[k] = monotoneCubicSpline(midiValues, values);
    }
  }

  return (midi: number): PhysicalParams => {
    const result: any = {};
    for (const k of allKeys) {
      let v = paramSplines[k](midi);
      if (INTEGER_PARAMS.has(k)) v = Math.round(v);
      result[k] = v;
    }
    return result as PhysicalParams;
  };
}

// ============================================================
// DEVIATION CALCULATION (physics-informed weighting)
// Based on Chabassier: lower partials are perceptually dominant,
// higher partials are less stable (σ_j = b1 + b3*ω²).
// Weight: w_j = 1 / (1 + (j-1)/5) — partial 1 has weight 1.0,
// partial 6 has 0.5, partial 11 has 0.33, etc.
// ============================================================

export function calculateDeviation(sample: Sample, modelHarmonics: Harmonic[]): number {
  const maxLen = Math.max(sample.harmonics?.length || 0, modelHarmonics.length);
  if (maxLen === 0) return 0;
  let totalWeightedError = 0;
  let totalWeight = 0;
  for (let i = 0; i < maxLen; i++) {
    const h1 = sample.harmonics?.[i]?.amplitude || 0;
    const h2 = modelHarmonics[i]?.amplitude || 0;
    const w = 1 / (1 + i / 5); // physics-informed: low partials matter more
    totalWeightedError += w * (h1 - h2) ** 2;
    totalWeight += w;
  }
  return Math.sqrt(totalWeightedError / totalWeight);
}

export function calculatePhysicalDeviation(sample: Sample, modelPhysical: PhysicalParams): number {
  const keys = Object.keys(modelPhysical) as (keyof PhysicalParams)[];
  if (keys.length === 0) return 0;
  let totalError = 0;
  let count = 0;
  keys.forEach(k => {
    const v1 = sample.physical![k];
    const v2 = modelPhysical[k];
    let error: number;
    if (LOG_SPACE_PARAMS.has(k) && v1 > 0 && v2 > 0) {
      // Log-ratio for log-space params (scale-invariant comparison)
      error = Math.abs(Math.log(v1 / v2));
    } else {
      // Relative error for linear params
      error = v2 !== 0 ? Math.abs((v1 - v2) / v2) : Math.abs(v1 - v2);
    }
    totalError += error ** 2;
    count++;
  });
  return Math.sqrt(totalError / count);
}

// ============================================================
// PHYSICS-CONSTRAINED CROSS-PARAMETER CONSISTENCY
// Based on:
//   f_n = n * F0 * sqrt(1 + B * n²)  →  B and f0 are coupled
//   σ_j = b1 + b3 * ω²               →  T60_fund and T60_nyq are coupled
//   B = π³Ed⁴ / (64TL²)              →  B depends on gauge
// ============================================================

/**
 * Compute cross-parameter consistency penalty for physical mode.
 * Returns a penalty value [0..1+] where 0 = perfectly consistent.
 */
function physicsConstraintPenalty(sample: Sample, model: PhysicalParams): number {
  const p = sample.physical;
  if (!p) return 0;

  let penalties: number[] = [];

  // 1. T60 ratio consistency: T60_fund / T60_nyq should follow damping model
  //    σ(f) = b1 + b3*ω², so T60 ~ 1/σ
  //    T60_fund corresponds to f0, T60_nyq corresponds to Nyquist (~20kHz)
  //    Expected ratio: T60_fund/T60_nyq ≈ (b1 + b3*ωnyq²) / (b1 + b3*ω0²) >> 1
  if (p.T60_fund > 0 && p.T60_nyq > 0 && model.T60_fund > 0 && model.T60_nyq > 0) {
    const sampleRatio = p.T60_fund / p.T60_nyq;
    const modelRatio = model.T60_fund / model.T60_nyq;
    if (modelRatio > 0) {
      penalties.push(Math.abs(Math.log(sampleRatio / modelRatio)));
    }
  }

  // 2. B vs f0 consistency: inharmonicity should be consistent with fundamental
  //    For neighboring notes, B*f0² should vary smoothly (since B ~ 1/L² and f0 ~ 1/L)
  //    So B*f0² ≈ const within a string group
  if (p.B > 0 && p.f0_hz > 0 && model.B > 0 && model.f0_hz > 0) {
    const sampleBf = p.B * p.f0_hz * p.f0_hz;
    const modelBf = model.B * model.f0_hz * model.f0_hz;
    if (modelBf > 0) {
      penalties.push(Math.abs(Math.log(sampleBf / modelBf)));
    }
  }

  // 3. Hammer mass vs gauge consistency: heavier strings need heavier hammers
  //    hammer_mass / string_mass ratio should be relatively stable across keyboard
  if (p.hammer_mass > 0 && p.string_mass > 0 && model.hammer_mass > 0 && model.string_mass > 0) {
    const sampleRatio = p.hammer_mass / p.string_mass;
    const modelRatio = model.hammer_mass / model.string_mass;
    if (modelRatio > 0) {
      penalties.push(0.5 * Math.abs(Math.log(sampleRatio / modelRatio)));
    }
  }

  // 4. Excitation point vs f0: exc_x0 typically scales with string length ~ 1/f0
  //    exc_x0 * f0 should be roughly constant (striking ratio)
  if (p.exc_x0 > 0 && p.f0_hz > 0 && model.exc_x0 > 0 && model.f0_hz > 0) {
    const sampleProduct = p.exc_x0 * p.f0_hz;
    const modelProduct = model.exc_x0 * model.f0_hz;
    if (modelProduct > 0) {
      penalties.push(0.5 * Math.abs(Math.log(sampleProduct / modelProduct)));
    }
  }

  if (penalties.length === 0) return 0;
  return Math.sqrt(_.mean(penalties.map(p => p * p)));
}

/**
 * Compute cross-parameter consistency for additive mode.
 * Checks that partial frequencies follow the inharmonicity law.
 */
function additiveConstraintPenalty(sample: Sample): number {
  if (!sample.additive || !sample.additive.partials || sample.additive.partials.length < 3) return 0;
  const { f0_hz, B, partials } = sample.additive;
  if (f0_hz <= 0) return 0;

  let penalties: number[] = [];

  // Check that partial frequencies follow f_n = n * f0 * sqrt(1 + B * n²)
  for (const p of partials) {
    if (p.k <= 0 || p.f_hz <= 0) continue;
    const n = p.k;
    const expectedF = n * f0_hz * Math.sqrt(1 + (B || 0) * n * n);
    if (expectedF > 0) {
      const freqError = Math.abs(Math.log(p.f_hz / expectedF));
      if (freqError > 0.001) { // ignore sub-0.1% deviations (measurement noise)
        penalties.push(freqError);
      }
    }
  }

  // Check decay consistency: tau1 should generally decrease with partial index
  // (higher partials decay faster: σ = b1 + b3*ω²)
  const taus = partials
    .filter(p => p.tau1 > 0 && p.k > 0)
    .sort((a, b) => a.k - b.k);
  if (taus.length >= 3) {
    let inversions = 0;
    for (let i = 1; i < taus.length; i++) {
      if (taus[i].tau1 > taus[i - 1].tau1 * 1.5) inversions++; // tau increasing = physically wrong
    }
    penalties.push(inversions / taus.length); // ratio of inversions
  }

  if (penalties.length === 0) return 0;
  return Math.sqrt(_.mean(penalties.map(p => p * p)));
}

// ============================================================
// MAIN ANALYSIS
// ============================================================

function matchesAnchor(sample: Sample, ref: AnchorRef): boolean {
  return ref.midi === sample.midi && ref.bankId === sample.bankId && (ref.vel === undefined || ref.vel === sample.vel);
}

export function analyzeDataset(
  allSamples: Sample[],
  anchorRefs: AnchorRef[],
  threshold: number = 0.1,
  mode: SynthesisMode,
  detection: DetectionMethod = 'per-parameter'
): AnalysisResult[] {
  const relevantSamples = allSamples.filter(s =>
    mode === 'additive' ? (s.harmonics && s.harmonics.length > 0) : !!s.physical
  );
  if (relevantSamples.length === 0) return [];

  const anchors = relevantSamples.filter(s =>
    anchorRefs.some(ref => matchesAnchor(s, ref))
  );

  const useConstraints = detection === 'physics-constrained';
  let results: AnalysisResult[];

  if (mode === 'additive') {
    const model = extractRelationships(anchors);
    results = relevantSamples.map(sample => {
      const isAnchor = anchorRefs.some(ref => matchesAnchor(sample, ref));
      const predicted = model(sample.midi);
      let deviation = isAnchor ? 0 : calculateDeviation(sample, predicted);
      // Add cross-parameter consistency penalty
      if (useConstraints && !isAnchor) {
        const penalty = additiveConstraintPenalty(sample);
        deviation = Math.sqrt(deviation * deviation + penalty * penalty);
      }
      return {
        midi: sample.midi, vel: sample.vel,
        bankId: sample.bankId, bankName: sample.bankName,
        deviation, isAnchor,
        isGood: isAnchor || deviation < threshold,
        predictedHarmonics: predicted,
      };
    });
  } else {
    const model = extractPhysicalRelationships(anchors);
    results = relevantSamples.map(sample => {
      const isAnchor = anchorRefs.some(ref => matchesAnchor(sample, ref));
      const predicted = model(sample.midi);
      let deviation = isAnchor ? 0 : calculatePhysicalDeviation(sample, predicted);
      // Add cross-parameter consistency penalty
      if (useConstraints && !isAnchor) {
        const penalty = physicsConstraintPenalty(sample, predicted);
        deviation = Math.sqrt(deviation * deviation + penalty * penalty);
      }
      return {
        midi: sample.midi,
        bankId: sample.bankId, bankName: sample.bankName,
        deviation, isAnchor,
        isGood: isAnchor || deviation < threshold,
        predictedPhysical: predicted,
      };
    });
  }

  return enrichWithStatistics(results);
}

// ============================================================
// STATISTICAL ENRICHMENT
// ============================================================

function enrichWithStatistics(results: AnalysisResult[]): AnalysisResult[] {
  if (results.length < 3) return results;
  const n = results.length;
  const deviations = results.map(r => r.deviation);
  const mean = _.mean(deviations);
  const stdDev = Math.sqrt(_.mean(deviations.map(d => (d - mean) ** 2)));

  // Precompute isolation scores in single O(n²) pass with k-selection buffer
  const k = Math.min(5, n - 1);
  const isolationScores = new Float64Array(n);

  // Pre-extract coordinates to avoid repeated property access
  const midiNorm = new Float64Array(n);
  const devs = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    midiNorm[i] = results[i].midi / 88;
    devs[i] = results[i].deviation;
  }

  for (let i = 0; i < n; i++) {
    // Maintain sorted k-nearest buffer (k is small, ~5, so linear insert is optimal)
    const kDists = new Float64Array(k);
    let kLen = 0;

    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const dM = midiNorm[j] - midiNorm[i];
      const dD = devs[j] - devs[i];
      const dist = Math.sqrt(dM * dM + dD * dD);

      if (kLen < k) {
        // Buffer not full yet — insert in sorted position
        let pos = kLen;
        while (pos > 0 && kDists[pos - 1] > dist) {
          kDists[pos] = kDists[pos - 1];
          pos--;
        }
        kDists[pos] = dist;
        kLen++;
      } else if (dist < kDists[k - 1]) {
        // Smaller than largest in buffer — replace and re-sort
        let pos = k - 2;
        while (pos >= 0 && kDists[pos] > dist) {
          kDists[pos + 1] = kDists[pos];
          pos--;
        }
        kDists[pos + 1] = dist;
      }
    }

    let sum = 0;
    for (let ki = 0; ki < kLen; ki++) sum += kDists[ki];
    isolationScores[i] = kLen > 0 ? sum / kLen : 0;
  }

  return results.map((r, i) => ({
    ...r,
    zScore: stdDev > 0 ? (r.deviation - mean) / stdDev : 0,
    isolationScore: isolationScores[i],
  }));
}

// ============================================================
// CORRECTION METHODS
// ============================================================

export function applyCorrection(
  results: AnalysisResult[],
  config: CorrectionConfig
): { corrected: AnalysisResult[], removed: number, kept: number } {
  let corrected: AnalysisResult[];

  switch (config.method) {
    case 'threshold':
      corrected = results.map(r => ({
        ...r,
        isGood: r.isAnchor || r.deviation < config.threshold,
      }));
      break;

    case 'zscore':
      corrected = results.map(r => ({
        ...r,
        isGood: r.isAnchor || (r.zScore !== undefined && Math.abs(r.zScore) < config.zScoreLimit),
      }));
      break;

    case 'iqr': {
      const devs = results.filter(r => !r.isAnchor).map(r => r.deviation).sort((a, b) => a - b);
      if (devs.length < 4) {
        corrected = results.map(r => ({ ...r, isGood: true }));
        break;
      }
      // Proper quartile calculation with linear interpolation
      const quantile = (arr: number[], p: number) => {
        const idx = p * (arr.length - 1);
        const lo = Math.floor(idx);
        const hi = Math.ceil(idx);
        return lo === hi ? arr[lo] : arr[lo] + (arr[hi] - arr[lo]) * (idx - lo);
      };
      const q1 = quantile(devs, 0.25);
      const q3 = quantile(devs, 0.75);
      const iqr = q3 - q1;
      const lowerBound = q1 - config.iqrMultiplier * iqr;
      const upperBound = q3 + config.iqrMultiplier * iqr;
      corrected = results.map(r => ({
        ...r,
        isGood: r.isAnchor || (r.deviation >= lowerBound && r.deviation <= upperBound),
      }));
      break;
    }

    case 'interpolate':
      corrected = results.map(r => ({ ...r, isGood: true }));
      break;

    default:
      corrected = results;
  }

  const removed = corrected.filter(r => !r.isGood).length;
  return { corrected, removed, kept: corrected.length - removed };
}

export function cleanDataset(results: AnalysisResult[], allSamples: Sample[]): Sample[] {
  const goodSet = new Set(results.filter(r => r.isGood).map(r => `${r.midi}:${r.bankId}`));
  return allSamples.filter(s => goodSet.has(`${s.midi}:${s.bankId}`));
}

// ============================================================
// COMPLETE BANK GENERATION
// Generates all 88 notes (MIDI 21-108) from anchor model.
// Physical: one note per MIDI. Additive: 8 velocity layers per MIDI.
// Uses original "good" samples where available, interpolated where not.
// ============================================================

// Additive-specific scalar params that need per-note interpolation
const ADDITIVE_SCALAR_KEYS: (keyof Omit<AdditiveNoteParams, 'partials' | 'eq_biquads'>)[] = [
  'f0_hz', 'B', 'phi_diff', 'attack_tau', 'A_noise', 'noise_centroid_hz', 'rms_gain',
];

const ADDITIVE_LOG_KEYS = new Set(['f0_hz', 'B', 'attack_tau', 'A_noise', 'noise_centroid_hz', 'rms_gain']);

const PARTIAL_KEYS: (keyof Partial)[] = ['f_hz', 'A0', 'tau1', 'tau2', 'a1', 'beat_hz', 'phi', 'fit_quality'];
const PARTIAL_LOG_KEYS = new Set(['f_hz', 'A0', 'tau1', 'tau2', 'beat_hz']);

/**
 * Build per-velocity additive splines from anchor samples.
 * Returns a function: (midi, vel) => AdditiveNoteParams
 */
function buildAdditiveModel(
  anchors: Sample[]
): (midi: number, vel: number) => AdditiveNoteParams {
  // Group by velocity layer
  const velGroups = _.groupBy(anchors.filter(s => s.additive), s => s.vel ?? 0);
  const availableVels = Object.keys(velGroups).map(Number).sort((a, b) => a - b);

  // For each vel layer, build splines for scalar params and per-partial params
  const velModels: Record<number, {
    scalarSplines: Record<string, (midi: number) => number>;
    partialCount: number;
    partialSplines: Record<string, (midi: number) => number>[]; // per partial index
    nStrings: (midi: number) => number;
    riseTau: (midi: number) => number;
  }> = {};

  for (const vel of availableVels) {
    const samples = velGroups[vel];
    const byMidi = _.groupBy(samples, 'midi');
    const averaged = Object.entries(byMidi).map(([midiStr, ss]) => {
      const midi = parseInt(midiStr);
      // Average additive params across banks at same midi+vel
      const avgAdditive: any = {};
      for (const k of ADDITIVE_SCALAR_KEYS) {
        const vals = ss.map(s => (s.additive as any)?.[k]).filter((v: any) => v !== undefined && v !== null);
        avgAdditive[k] = vals.length > 0 ? _.mean(vals) : 0;
      }
      // Average partials
      const maxPartials = _.max(ss.map(s => s.additive?.partials.length || 0)) || 0;
      const avgPartials: any[] = [];
      for (let pi = 0; pi < maxPartials; pi++) {
        const partial: any = {};
        for (const pk of PARTIAL_KEYS) {
          const vals = ss.map(s => s.additive?.partials[pi]?.[pk]).filter((v: any) => v !== undefined && v !== null && !isNaN(v as number));
          partial[pk] = vals.length > 0 ? _.mean(vals as number[]) : 0;
        }
        partial.k = pi + 1;
        avgPartials.push(partial);
      }
      return { midi, additive: avgAdditive, partials: avgPartials, nStrings: ss[0].additive?.n_strings ?? 1, riseTau: ss[0].additive?.rise_tau ?? 0 };
    });
    const sorted = _.sortBy(averaged, 'midi');
    const midis = sorted.map(a => a.midi);

    // Build scalar splines
    const scalarSplines: Record<string, (midi: number) => number> = {};
    for (const k of ADDITIVE_SCALAR_KEYS) {
      const vals = sorted.map(a => a.additive[k] as number);
      scalarSplines[k] = (ADDITIVE_LOG_KEYS.has(k) && vals.every(v => v > 0))
        ? logSpaceSpline(midis, vals)
        : monotoneCubicSpline(midis, vals);
    }

    // Build per-partial splines
    const maxPartials = _.max(sorted.map(a => a.partials.length)) || 0;
    const partialSplines: Record<string, (midi: number) => number>[] = [];
    for (let pi = 0; pi < maxPartials; pi++) {
      const pSplines: Record<string, (midi: number) => number> = {};
      for (const pk of PARTIAL_KEYS) {
        const vals = sorted.map(a => (a.partials[pi]?.[pk] as number) || 0);
        pSplines[pk] = (PARTIAL_LOG_KEYS.has(pk) && vals.every(v => v > 0))
          ? logSpaceSpline(midis, vals)
          : monotoneCubicSpline(midis, vals);
      }
      partialSplines.push(pSplines);
    }

    const nStringsSpline = monotoneCubicSpline(midis, sorted.map(a => a.nStrings));
    const riseTauSpline = monotoneCubicSpline(midis, sorted.map(a => a.riseTau));

    velModels[vel] = { scalarSplines, partialCount: maxPartials, partialSplines, nStrings: nStringsSpline, riseTau: riseTauSpline };
  }

  return (midi: number, vel: number): AdditiveNoteParams => {
    // Find nearest velocity layer model (or interpolate between two)
    let model = velModels[availableVels[0]];
    if (velModels[vel]) {
      model = velModels[vel];
    } else {
      // Find closest available vel
      const closest = _.minBy(availableVels, v => Math.abs(v - vel));
      model = velModels[closest!];
    }

    const result: any = {};
    for (const k of ADDITIVE_SCALAR_KEYS) {
      result[k] = model.scalarSplines[k](midi);
    }
    result.n_strings = Math.round(model.nStrings(midi));
    result.rise_tau = model.riseTau(midi);

    // Generate partials using inharmonicity formula: f_n = n * f0 * sqrt(1 + B * n²)
    const f0 = result.f0_hz;
    const B = result.B;
    result.partials = [];
    for (let pi = 0; pi < model.partialCount; pi++) {
      const partial: any = {};
      for (const pk of PARTIAL_KEYS) {
        partial[pk] = model.partialSplines[pi][pk](midi);
      }
      partial.k = pi + 1;
      // Enforce physics: partial frequency must follow inharmonicity law
      if (f0 > 0) {
        const n = pi + 1;
        partial.f_hz = n * f0 * Math.sqrt(1 + (B || 0) * n * n);
      }
      partial.A0 = Math.max(0, partial.A0);
      partial.tau1 = Math.max(0, partial.tau1);
      partial.tau2 = Math.max(0, partial.tau2);
      result.partials.push(partial as Partial);
    }

    return result as AdditiveNoteParams;
  };
}

export interface CompleteBankExport {
  metadata: Record<string, any>;
  notes: Record<string, any>;
  stats: { totalNotes: number; fromOriginal: number; interpolated: number };
}

/**
 * Generate a complete bank with all 88 notes (MIDI 21-108).
 * Physical: 88 notes. Additive: 88 notes × 8 velocity layers.
 * Uses original "good" samples where available, interpolated values where missing.
 */
export function generateCompleteBank(
  allSamples: Sample[],
  results: AnalysisResult[],
  anchorRefs: AnchorRef[],
  mode: SynthesisMode,
  bankMeta: { correctionMethod: string; threshold: number; sourceNames: string[] }
): CompleteBankExport {
  const MIDI_MIN = 21;
  const MIDI_MAX = 108;

  const anchors = allSamples.filter(s =>
    anchorRefs.some(ref => matchesAnchor(s, ref))
  );

  const goodResults = new Map<string, AnalysisResult>();
  results.filter(r => r.isGood).forEach(r => {
    const key = mode === 'additive' ? `${r.midi}:${r.vel ?? 0}` : `${r.midi}`;
    // Keep anchor or best (lowest deviation) result per note
    const existing = goodResults.get(key);
    if (!existing || r.isAnchor || r.deviation < existing.deviation) {
      goodResults.set(key, r);
    }
  });

  const goodSamples = new Map<string, Sample>();
  allSamples.forEach(s => {
    const key = mode === 'additive' ? `${s.midi}:${s.vel ?? 0}` : `${s.midi}`;
    if (goodResults.has(key)) {
      const r = goodResults.get(key)!;
      if (s.midi === r.midi && s.bankId === r.bankId) {
        goodSamples.set(key, s);
      }
    }
  });

  const notes: Record<string, any> = {};
  let fromOriginal = 0;
  let interpolated = 0;

  if (mode === 'physical') {
    const physModel = extractPhysicalRelationships(anchors);

    for (let midi = MIDI_MIN; midi <= MIDI_MAX; midi++) {
      const midiPad = String(midi).padStart(3, '0');
      const key = `m${midiPad}`;
      const lookupKey = `${midi}`;
      const sample = goodSamples.get(lookupKey);

      if (sample?.physical) {
        notes[key] = { midi, ...sample.physical };
        fromOriginal++;
      } else {
        notes[key] = { midi, ...physModel(midi) };
        interpolated++;
      }
    }
  } else {
    // Additive: 88 notes × 8 velocity layers
    const addModel = buildAdditiveModel(anchors);

    for (let midi = MIDI_MIN; midi <= MIDI_MAX; midi++) {
      const midiPad = String(midi).padStart(3, '0');

      for (let vel = 0; vel < 8; vel++) {
        const noteKey = `m${midiPad}_vel${vel}`;
        const lookupKey = `${midi}:${vel}`;
        const sample = goodSamples.get(lookupKey);

        if (sample?.additive) {
          notes[noteKey] = { midi, vel, ...sample.additive };
          fromOriginal++;
        } else {
          const generated = addModel(midi, vel);
          notes[noteKey] = { midi, vel, ...generated };
          interpolated++;
        }
      }
    }
  }

  return {
    metadata: {
      name: `Complete ${mode} bank`,
      core: mode === 'additive' ? 'AdditiveSynthesisPianoCore' : 'PhysicalModelingPianoCore',
      generated_by: "ICR Soundbank Analyzer v4.0",
      timestamp: new Date().toISOString(),
      correction_method: bankMeta.correctionMethod,
      threshold: bankMeta.threshold,
      source_banks: bankMeta.sourceNames,
      generation: 'complete',
      midi_range: [MIDI_MIN, MIDI_MAX],
      velocity_layers: mode === 'additive' ? 8 : 1,
    },
    notes,
    stats: {
      totalNotes: Object.keys(notes).length,
      fromOriginal,
      interpolated,
    },
  };
}
