import { Sample, SynthesisMode, AnalysisResult, ProjectedPoint, PHYSICAL_PARAM_KEYS, AnchorRef } from '../types';
import _ from 'lodash';

export type ProjectionMethod = 'pca' | 'spectral' | 'parameter-pair';

export interface ProjectionConfig {
  method: ProjectionMethod;
  // For 'parameter-pair' mode: which two params to use as x/y
  paramX?: string;
  paramY?: string;
}

/**
 * Extract a feature vector from a sample for projection.
 */
function sampleToVector(sample: Sample, mode: SynthesisMode): number[] {
  if (mode === 'physical' && sample.physical) {
    return PHYSICAL_PARAM_KEYS.map(k => sample.physical![k] ?? 0);
  }
  if (mode === 'additive' && sample.harmonics) {
    // Use first N harmonic amplitudes as features
    const N = Math.min(sample.harmonics.length, 20);
    const vec: number[] = [];
    for (let i = 0; i < N; i++) {
      vec.push(sample.harmonics[i]?.amplitude || 0);
    }
    // Pad to N if shorter
    while (vec.length < 20) vec.push(0);
    return vec;
  }
  return [];
}

/**
 * Normalize columns to zero mean, unit variance.
 */
function normalizeMatrix(matrix: number[][]): { normalized: number[][], means: number[], stds: number[] } {
  if (matrix.length === 0) return { normalized: [], means: [], stds: [] };
  const nCols = matrix[0].length;
  const means = Array(nCols).fill(0);
  const stds = Array(nCols).fill(0);

  for (let j = 0; j < nCols; j++) {
    const col = matrix.map(row => row[j]);
    means[j] = _.mean(col);
    stds[j] = Math.sqrt(_.mean(col.map(v => (v - means[j]) ** 2))) || 1;
  }

  const normalized = matrix.map(row =>
    row.map((v, j) => (v - means[j]) / stds[j])
  );

  return { normalized, means, stds };
}

/**
 * Simple PCA: compute top-2 principal components via power iteration on covariance matrix.
 */
function pcaProject(matrix: number[][]): { x: number[], y: number[] } {
  const n = matrix.length;
  if (n === 0) return { x: [], y: [] };
  const d = matrix[0].length;

  // Covariance matrix (d x d)
  const cov: number[][] = Array.from({ length: d }, () => Array(d).fill(0));
  for (let i = 0; i < d; i++) {
    for (let j = i; j < d; j++) {
      let sum = 0;
      for (let k = 0; k < n; k++) {
        sum += matrix[k][i] * matrix[k][j];
      }
      cov[i][j] = sum / n;
      cov[j][i] = cov[i][j];
    }
  }

  // Power iteration for first eigenvector
  const pc1 = powerIteration(cov, d);
  // Deflate
  const deflated = cov.map((row, i) =>
    row.map((v, j) => v - pc1[i] * pc1[j] * dotProduct(matVecMul(cov, pc1), pc1))
  );
  const pc2 = powerIteration(deflated, d);

  // Project
  const x = matrix.map(row => dotProduct(row, pc1));
  const y = matrix.map(row => dotProduct(row, pc2));

  return { x, y };
}

function powerIteration(mat: number[][], d: number, iters: number = 50): number[] {
  let v = Array.from({ length: d }, () => Math.random() - 0.5);
  let norm = Math.sqrt(dotProduct(v, v));
  v = v.map(x => x / norm);

  for (let iter = 0; iter < iters; iter++) {
    const Av = matVecMul(mat, v);
    norm = Math.sqrt(dotProduct(Av, Av));
    if (norm < 1e-10) break;
    v = Av.map(x => x / norm);
  }
  return v;
}

function matVecMul(mat: number[][], vec: number[]): number[] {
  return mat.map(row => dotProduct(row, vec));
}

function dotProduct(a: number[], b: number[]): number {
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += a[i] * b[i];
  return sum;
}

/**
 * Spectral projection: uses spectral centroid + spectral spread for additive,
 * or frequency/decay for physical.
 */
function spectralProject(samples: Sample[], mode: SynthesisMode): { x: number[], y: number[] } {
  const x: number[] = [];
  const y: number[] = [];

  for (const s of samples) {
    if (mode === 'additive' && s.harmonics) {
      const totalAmp = _.sumBy(s.harmonics, 'amplitude') || 1;
      // Spectral centroid
      const centroid = _.sumBy(s.harmonics, h => h.index * h.amplitude) / totalAmp;
      // Spectral spread (variance around centroid)
      const spread = Math.sqrt(
        _.sumBy(s.harmonics, h => h.amplitude * (h.index - centroid) ** 2) / totalAmp
      );
      x.push(centroid);
      y.push(spread);
    } else if (mode === 'physical' && s.physical) {
      // X: log frequency, Y: decay ratio
      x.push(Math.log2(s.physical.f0_hz || 1));
      y.push(s.physical.T60_fund / (s.physical.T60_nyq || 0.01));
    } else {
      x.push(0);
      y.push(0);
    }
  }

  return { x, y };
}

/**
 * Direct parameter-pair projection: just pick two named parameters.
 */
function paramPairProject(
  samples: Sample[],
  mode: SynthesisMode,
  paramX: string,
  paramY: string
): { x: number[], y: number[] } {
  const x: number[] = [];
  const y: number[] = [];

  for (const s of samples) {
    if (mode === 'physical' && s.physical) {
      x.push((s.physical as any)[paramX] ?? 0);
      y.push((s.physical as any)[paramY] ?? 0);
    } else if (mode === 'additive' && s.harmonics) {
      // For additive, paramX/Y can be harmonic indices like "h1", "h2" etc.
      const getVal = (param: string) => {
        const hMatch = param.match(/^h(\d+)$/);
        if (hMatch) {
          const idx = parseInt(hMatch[1]) - 1;
          return s.harmonics?.[idx]?.amplitude || 0;
        }
        if (param === 'f0_hz') return s.additive?.f0_hz || 0;
        if (param === 'rms_gain') return s.additive?.rms_gain || 0;
        if (param === 'A_noise') return s.additive?.A_noise || 0;
        if (param === 'attack_tau') return s.additive?.attack_tau || 0;
        return 0;
      };
      x.push(getVal(paramX));
      y.push(getVal(paramY));
    } else {
      x.push(0);
      y.push(0);
    }
  }

  return { x, y };
}

/**
 * Main projection function: project samples into 2D space.
 */
export function projectSamples(
  samples: Sample[],
  mode: SynthesisMode,
  results: AnalysisResult[],
  anchorRefs: AnchorRef[],
  config: ProjectionConfig
): ProjectedPoint[] {
  if (samples.length === 0) return [];

  let coords: { x: number[], y: number[] };

  switch (config.method) {
    case 'pca': {
      const vectors = samples.map(s => sampleToVector(s, mode)).filter(v => v.length > 0);
      if (vectors.length === 0) return [];
      const { normalized } = normalizeMatrix(vectors);
      coords = pcaProject(normalized);
      break;
    }
    case 'spectral':
      coords = spectralProject(samples, mode);
      break;
    case 'parameter-pair':
      coords = paramPairProject(samples, mode, config.paramX || 'f0_hz', config.paramY || 'T60_fund');
      break;
    default:
      coords = spectralProject(samples, mode);
  }

  return samples.map((s, i) => {
    const res = results.find(r => r.midi === s.midi && r.bankId === s.bankId);
    const isAnchor = anchorRefs.some(ref => ref.midi === s.midi && ref.bankId === s.bankId && (ref.vel === undefined || ref.vel === s.vel));
    return {
      x: coords.x[i] || 0,
      y: coords.y[i] || 0,
      midi: s.midi,
      vel: s.vel,
      bankName: s.bankName,
      bankId: s.bankId,
      isGood: res?.isGood ?? true,
      isAnchor,
      deviation: res?.deviation ?? 0,
    };
  });
}

/**
 * Compute pairwise tension (distance) between samples for tension graph.
 * Returns edges with source/target indices and distance.
 */
export function computeTensionEdges(
  samples: Sample[],
  mode: SynthesisMode,
  maxEdges: number = 300
): { source: number, target: number, distance: number }[] {
  const vectors = samples.map(s => sampleToVector(s, mode));
  const { normalized } = normalizeMatrix(vectors);
  const n = normalized.length;

  // Compute k-nearest neighbor edges
  const k = Math.min(5, n - 1);
  const edgeMap = new Map<string, { source: number, target: number, distance: number }>();

  for (let i = 0; i < n; i++) {
    const dists: { j: number, d: number }[] = [];
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      let d = 0;
      for (let f = 0; f < normalized[i].length; f++) {
        d += (normalized[i][f] - normalized[j][f]) ** 2;
      }
      dists.push({ j, d: Math.sqrt(d) });
    }
    dists.sort((a, b) => a.d - b.d);
    for (let ki = 0; ki < k; ki++) {
      const { j, d } = dists[ki];
      const key = `${Math.min(i, j)}-${Math.max(i, j)}`;
      if (!edgeMap.has(key)) {
        edgeMap.set(key, { source: Math.min(i, j), target: Math.max(i, j), distance: d });
      }
    }
  }

  const edges = Array.from(edgeMap.values());
  edges.sort((a, b) => a.distance - b.distance);
  return edges.slice(0, maxEdges);
}

/**
 * Get available projection parameter options for the UI.
 */
export function getProjectionParams(mode: SynthesisMode): { value: string, label: string }[] {
  if (mode === 'physical') {
    return PHYSICAL_PARAM_KEYS.map(k => ({ value: k, label: k }));
  }
  return [
    { value: 'h1', label: 'Harmonic 1 (A0)' },
    { value: 'h2', label: 'Harmonic 2' },
    { value: 'h3', label: 'Harmonic 3' },
    { value: 'h4', label: 'Harmonic 4' },
    { value: 'h5', label: 'Harmonic 5' },
    { value: 'h10', label: 'Harmonic 10' },
    { value: 'h20', label: 'Harmonic 20' },
    { value: 'f0_hz', label: 'F0 (Hz)' },
    { value: 'rms_gain', label: 'RMS Gain' },
    { value: 'A_noise', label: 'Noise Amplitude' },
    { value: 'attack_tau', label: 'Attack Tau' },
  ];
}
