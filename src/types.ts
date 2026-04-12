export type SynthesisMode = 'additive' | 'physical';

// --- Physical model types ---
export interface PhysicalParams {
  f0_hz: number;
  B: number;
  gauge: number;
  T60_fund: number;
  T60_nyq: number;
  exc_x0: number;
  K_hardening: number;
  p_hardening: number;
  n_disp_stages: number;
  disp_coeff: number;
  n_strings: number;
  detune_cents: number;
  hammer_mass: number;
  string_mass: number;
  output_scale: number;
  bridge_refl: number;
}

export const PHYSICAL_PARAM_KEYS: (keyof PhysicalParams)[] = [
  'f0_hz', 'B', 'gauge', 'T60_fund', 'T60_nyq', 'exc_x0',
  'K_hardening', 'p_hardening', 'n_disp_stages', 'disp_coeff',
  'n_strings', 'detune_cents', 'hammer_mass', 'string_mass',
  'output_scale', 'bridge_refl'
];

// --- Additive model types ---
export interface Partial {
  k: number;
  f_hz: number;
  A0: number;
  tau1: number;
  tau2: number;
  a1: number;
  beat_hz: number;
  phi: number;
  fit_quality: number;
}

export interface AdditiveNoteParams {
  f0_hz: number;
  B: number;
  phi_diff: number;
  attack_tau: number;
  A_noise: number;
  noise_centroid_hz: number;
  rms_gain: number;
  n_strings?: number;
  rise_tau?: number;
  partials: Partial[];
  eq_biquads?: any[];
}

// Legacy compat - simplified harmonic view
export interface Harmonic {
  index: number;
  amplitude: number;
  phase?: number;
}

// --- Unified sample ---
export interface Sample {
  midi: number;
  vel?: number;
  bankId: string;
  bankName: string;
  physical?: PhysicalParams;
  additive?: AdditiveNoteParams;
  harmonics?: Harmonic[];
}

export interface Soundbank {
  id: string;
  name: string;
  mode: SynthesisMode;
  samples: Sample[];
  metadata?: Record<string, any>;
}

export interface SoundbankMetadata {
  name: string;
  path: string;
  download_url: string;
}

export interface AnalysisResult {
  midi: number;
  vel?: number;
  bankId: string;
  bankName: string;
  deviation: number;
  isAnchor: boolean;
  isGood: boolean;
  predictedHarmonics?: Harmonic[];
  predictedPhysical?: PhysicalParams;
  zScore?: number;
  isolationScore?: number;
}

// --- Anchor reference ---
export interface AnchorRef {
  midi: number;
  vel?: number;
  bankId: string;
}

// --- Projection types ---
export interface ProjectedPoint {
  x: number;
  y: number;
  midi: number;
  vel?: number;
  bankName: string;
  bankId: string;
  isGood: boolean;
  isAnchor: boolean;
  deviation: number;
}

export type DetectionMethod =
  | 'per-parameter'       // Each param interpolated & compared independently
  | 'physics-constrained'; // Cross-parameter consistency checks (B vs f0, T60 ratio, etc.)

export type CorrectionMethod =
  | 'threshold'
  | 'zscore'
  | 'iqr'
  | 'interpolate';

export interface CorrectionConfig {
  method: CorrectionMethod;
  threshold: number;
  zScoreLimit: number;
  iqrMultiplier: number;
}

export interface LogEntry {
  timestamp: string;
  level: 'info' | 'debug' | 'error' | 'success';
  message: string;
}
