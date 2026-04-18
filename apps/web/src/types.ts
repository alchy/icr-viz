/**
 * TypeScript mirrors of piano_web/schemas.py — must stay in sync with the
 * FastAPI response shapes. Any drift surfaces as a runtime TanStack Query error.
 */

export type Origin = 'measured' | 'derived' | 'extrapolated' | 'anchored';

export interface BankSummary {
  id: string;
  parent_id: string | null;
  instrument: string | null;
  created_at: string | null;
}

export interface BankDetail {
  id: string;
  parent_id: string | null;
  instrument: string | null;
  n_notes: number;
  velocities: number[];
  midi_range: [number, number] | null;
  k_max: number | null;
  created_at: string | null;
  source: string | null;
  metadata: Record<string, unknown>;
}

export interface NoteIndex {
  midi: number;
  velocity: number;
}

export interface PartialDetail {
  k: number;
  f_hz: number;
  A0: number;
  tau1: number;
  tau2: number;
  a1: number;
  beat_hz: number;
  phi: number;
  fit_quality: number;
  sigma: number | null;
  origin: Origin;
}

export interface NoteDetail {
  midi: number;
  velocity: number;
  f0_hz: number;
  B: number;
  phi_diff: number;
  attack_tau: number;
  A_noise: number;
  noise_centroid_hz: number;
  rms_gain: number;
  n_strings: number | null;
  rise_tau: number | null;
  stereo_width: number | null;
  partials: PartialDetail[];
}

export interface CurvePoint {
  k: number;
  value: number;
  sigma: number | null;
  fit_quality: number;
  origin: Origin;
}

export interface CurvesPayload {
  midi: number;
  velocity: number;
  parameters: Record<string, CurvePoint[]>;
}

// Parameters supported by the math layer — must match piano_core.constants.MATH_PARAMS.
export const MATH_PARAMS = ['tau1', 'tau2', 'A0', 'a1', 'beat_hz', 'f_coef'] as const;
export type MathParam = typeof MATH_PARAMS[number];

// Which params are plotted on a log-y axis by default — large dynamic range.
export const LOG_Y_PARAMS: readonly MathParam[] = ['tau1', 'tau2', 'A0'];


// ---- i2 anchor schemas ---------------------------------------------------

export type AnchorOrigin = 'manual' | 'imported' | 'regression_fit';

export interface AnchorDetail {
  id: string;
  midi: number;
  velocity: number;
  k: number;
  parameter: MathParam;
  value: number;
  weight: number;
  origin: AnchorOrigin;
  created_at: string;
  created_by: string;
  note: string;
}

export interface AnchorCreate {
  k: number;
  parameter: MathParam;
  value: number;
  weight?: number;
  origin?: AnchorOrigin;
  created_by?: string;
  note?: string;
}

export interface AnchorPatch {
  value?: number;
  weight?: number;
  note?: string;
}

export interface AnchorMutationResponse {
  new_bank_id: string;
  parent_id: string | null;
  anchor: AnchorDetail | null;
}

export interface AnchorInterpolateParams {
  target_note_ids: Array<[number, number]>;
  parameters: MathParam[];
  prior_weight?: number;
  smoothing?: number | null;
  k_range?: [number, number] | null;
  commit?: boolean;
  random_seed?: number;
}

export interface ParameterCurveDiag {
  midi: number;
  velocity: number;
  parameter: MathParam;
  k_grid: number[];
  values: number[];
  sigmas: number[];
  lambda_used: number;
  used_pchip: boolean;
  coverage: [number, number];
  n_observations: number;
  n_anchors_used: number;
  warnings: string[];
}

export interface AnchorInterpolateResponse {
  new_bank_id: string | null;
  parent_id: string | null;
  per_parameter: ParameterCurveDiag[];
}


// ---- i3: ToneIdentifyAndCorrect + DeviationReport ------------------------

export interface ToneCorrectParams {
  target_note_id: [number, number];
  reference_bank_ids: string[];
  parameters?: MathParam[];
  use_anchors?: boolean;
  use_physical_prior?: boolean;
  preserve_fundamental?: boolean;
  noise_threshold_d?: number;
  correction_threshold_d?: number;
  fill_quality_threshold?: number;
  fallback_on_insufficient?: 'error' | 'skip' | 'prior_only';
  min_sources_for_consensus?: number;
  commit?: boolean;
  random_seed?: number;
}

export interface ToneIdentifyOnlyResponse {
  target_note_id: [number, number];
  reference_bank_ids: string[];
  reference_summary: Record<string, unknown>;
}

export interface PerPartialLogEntry {
  k: number;
  parameter: string;
  action: 'none' | 'fill' | 'soft_blend' | 'hard_replace';
  current: number | null;
  reference: number;
  reference_sigma: number;
  reference_quality: number;
  normalized_deviation: number | null;
  new_value: number | null;
  reason: string;
}

export interface ToneCorrectResponse {
  new_bank_id: string | null;
  parent_id: string | null;
  target_note_id: [number, number];
  reference_bank_ids: string[];
  reference_summary: Record<string, unknown>;
  per_partial_log: PerPartialLogEntry[];
  n_changed: number;
  n_filled: number;
  n_unchanged: number;
  warnings: string[];
}

export interface DeviationEntry {
  midi: number;
  velocity: number;
  k: number;
  parameter: string;
  target_value: number;
  reference_value: number;
  reference_sigma: number;
  z_score: number;
  recommend_action: 'review' | 'soft_correction' | 'hard_correction' | 'fill';
}

export interface DeviationReport {
  target_bank_id: string;
  reference_bank_ids: string[];
  loo: boolean;
  min_z: number;
  parameters: string[];
  entries: DeviationEntry[];
  n_entries: number;
}


// ---- i3.6: SplineTransfer ---------------------------------------------

export type TransferMode = 'absolute' | 'relative' | 'delta';

export interface SplineTransferConfig {
  parameter: MathParam;
  mode: TransferMode;
  preserve_fundamental?: boolean;
  clamp_to_bounds?: boolean;
  source_smoothing?: number | null;
}

export interface SplineTransferParams {
  source_bank_id?: string | null;
  source_note_id: [number, number];
  target_note_ids: Array<[number, number]>;
  parameter_configs?: SplineTransferConfig[];
  legacy_parameter?: MathParam;
  legacy_mode?: TransferMode;
  commit?: boolean;
  random_seed?: number;
}

export interface SplineTransferResponse {
  new_bank_id: string | null;
  parent_id: string | null;
  source_bank_id: string;
  source_note_id: [number, number];
  target_note_ids: Array<[number, number]>;
  parameter_configs: SplineTransferConfig[];
  warnings: string[];
}


// ---- i4: Math analysis --------------------------------------------------

export interface NoteMathDiag {
  midi: number;
  velocity: number;
  f0_hz: number;
  B_hat: number | null;
  B_sigma: number | null;
  tau1_alpha: number | null;
  tau1_alpha_sigma: number | null;
  tau2_alpha: number | null;
  tau2_alpha_sigma: number | null;
  A0_beta: number | null;
  A0_mu: number | null;
  gamma: number | null;
  fit_rmse: Record<string, number>;
  physical_prior_fit_quality: number;
  warnings: string[];
}

export interface TrendAnalysis {
  n: number;
  median: number | null;
  mean: number | null;
  iqr: [number, number] | null;
  r_squared: number | null;
}

export interface RatioStats {
  n: number;
  median: number | null;
  mean: number | null;
  min: number | null;
  max: number | null;
  p05: number | null;
  p95: number | null;
}

export interface MonotonicityViolation {
  parameter: string;
  velocity: number;
  k: number;
  midi_from: number;
  midi_to: number;
  expected_direction: 'decreasing' | 'increasing';
  delta: number;
  severity: 'minor' | 'major';
}

export interface OutlierNote {
  midi: number;
  velocity: number;
  attribute: string;
  value: number;
  median: number;
  mad_z: number;
  severity: 'warn' | 'strong';
}

export interface MathAnalysisReport {
  bank_id: string;
  generated_at: string;
  n_notes: number;
  per_note: Record<string, NoteMathDiag>;
  inharmonicity_trend: TrendAnalysis;
  tau1_power_trend: TrendAnalysis;
  tau2_power_trend: TrendAnalysis;
  A0_beta_distribution: TrendAnalysis;
  A0_mu_distribution: TrendAnalysis;
  gamma_ratio_stats: RatioStats;
  monotonicity_violations: MonotonicityViolation[];
  outlier_notes: OutlierNote[];
}

export interface CrossNoteSeries {
  bank_id: string;
  parameter: string;
  velocity: number;
  k: number;
  n: number;
  series: Array<{midi: number; value: number}>;
}


// ---- i5: BankIntegrity + Export + Surface ----------------------------

export type IntegrityIssueKind =
  | 'monotonicity'
  | 'missing_note'
  | 'quality_floor'
  | 'physical_consistency'
  | 'inharmonicity_range'
  | 'tau_ordering';

export type IntegrityIssueSeverity = 'info' | 'warning' | 'error';

export interface IntegrityIssue {
  kind: IntegrityIssueKind;
  severity: IntegrityIssueSeverity;
  location: {
    midi: number;
    velocity: number;
    k: number | null;
    parameter: string | null;
  };
  detail: string;
  suggested_operator: string | null;
  suggested_params: Record<string, unknown> | null;
}

export interface BankIntegrityResponse {
  bank_id: string;
  ok: boolean;
  n_issues: number;
  n_errors: number;
  n_warnings: number;
  issues: IntegrityIssue[];
}

export type ExportFormat = 'icr' | 'synth_csv' | 'analysis_csv' | 'ndjson';

export interface SurfaceGrid {
  bank_id: string;
  parameter: string;
  velocity: number;
  color_by: 'value' | 'fit_quality' | 'origin';
  midi: number[];
  k: number[];
  z: Array<Array<number | null>>;
  color: Array<Array<number | null>> | null;
  origin: Array<Array<string | null>> | null;
}


// ---- i6: MIDI bridge + ICR process ---------------------------------------

export interface MidiPorts {
  input_ports: string[];
  output_ports: string[];
}

export interface MidiStatus {
  input_open: boolean;
  output_open: boolean;
  input_port_name: string | null;
  output_port_name: string | null;
  last_pong_ts: number | null;
}

export interface MidiPingResponse {
  ok: boolean;
  rtt_ms: number | null;
}

export type IcrCore = 'active' | 'additive' | 'physical' | 'sampler' | 'sine' | 'iff';

export interface IcrStatus {
  running: boolean;
  pid: number | null;
  path: string | null;
  started_at: number | null;
  uptime_s: number | null;
  return_code: number | null;
  args: string[];
}

export interface IcrSettings {
  icr_path: string | null;
}
