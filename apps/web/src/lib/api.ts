/**
 * Thin fetch wrappers for the piano_web API.
 *
 * No caching here — that's TanStack Query's job. These are just typed calls
 * to `/api/*` endpoints. In dev, Vite proxies `/api` to the FastAPI backend
 * (see vite.config.ts). In production the frontend is served same-origin by
 * FastAPI itself.
 */

import type {
  AnchorCreate,
  AnchorDetail,
  AnchorInterpolateParams,
  AnchorInterpolateResponse,
  AnchorMutationResponse,
  AnchorPatch,
  BankDetail,
  BankIntegrityResponse,
  BankSummary,
  CrossNoteSeries,
  CurvesPayload,
  DeviationReport,
  ExportFormat,
  MathAnalysisReport,
  NoteDetail,
  NoteIndex,
  NoteMathDiag,
  SplineTransferParams,
  SplineTransferResponse,
  SurfaceGrid,
  ToneCorrectParams,
  ToneCorrectResponse,
  ToneIdentifyOnlyResponse,
} from '@/types';

export class ApiError extends Error {
  status: number;
  url: string;

  constructor(status: number, url: string, detail: string) {
    super(`${status} ${url}: ${detail}`);
    this.status = status;
    this.url = url;
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, {headers: {Accept: 'application/json'}});
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // response wasn't JSON — keep statusText
    }
    throw new ApiError(res.status, url, detail);
  }
  return (await res.json()) as T;
}

async function sendJson<T>(
  method: 'POST' | 'PATCH' | 'DELETE',
  url: string,
  body?: unknown,
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = await res.json();
      if (typeof errBody?.detail === 'string') detail = errBody.detail;
      else if (errBody?.detail) detail = JSON.stringify(errBody.detail);
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, url, detail);
  }
  return (await res.json()) as T;
}

export function listBanks(): Promise<BankSummary[]> {
  return getJson('/api/banks');
}

export function getBank(bankId: string): Promise<BankDetail> {
  return getJson(`/api/banks/${encodeURIComponent(bankId)}`);
}

export function listNotes(bankId: string): Promise<NoteIndex[]> {
  return getJson(`/api/banks/${encodeURIComponent(bankId)}/notes`);
}

export function getNote(
  bankId: string,
  midi: number,
  velocity: number,
): Promise<NoteDetail> {
  return getJson(
    `/api/banks/${encodeURIComponent(bankId)}/notes/${midi}/${velocity}`,
  );
}

export function getCurves(
  bankId: string,
  midi: number,
  velocity: number,
  parameters?: string[],
): Promise<CurvesPayload> {
  const base = `/api/banks/${encodeURIComponent(bankId)}/notes/${midi}/${velocity}/curves`;
  if (!parameters || parameters.length === 0) return getJson(base);
  const qs = parameters.map((p) => `parameters=${encodeURIComponent(p)}`).join('&');
  return getJson(`${base}?${qs}`);
}


// ---- i2 anchor endpoints ------------------------------------------------

export function listAnchors(
  bankId: string,
  midi: number,
  velocity: number,
): Promise<AnchorDetail[]> {
  return getJson(`/api/banks/${encodeURIComponent(bankId)}/notes/${midi}/${velocity}/anchors`);
}

export function createAnchor(
  bankId: string,
  midi: number,
  velocity: number,
  body: AnchorCreate,
): Promise<AnchorMutationResponse> {
  return sendJson(
    'POST',
    `/api/banks/${encodeURIComponent(bankId)}/notes/${midi}/${velocity}/anchors`,
    body,
  );
}

export function patchAnchor(
  bankId: string,
  anchorId: string,
  body: AnchorPatch,
): Promise<AnchorMutationResponse> {
  return sendJson(
    'PATCH',
    `/api/banks/${encodeURIComponent(bankId)}/anchors/${encodeURIComponent(anchorId)}`,
    body,
  );
}

export function deleteAnchor(
  bankId: string,
  anchorId: string,
): Promise<AnchorMutationResponse> {
  return sendJson(
    'DELETE',
    `/api/banks/${encodeURIComponent(bankId)}/anchors/${encodeURIComponent(anchorId)}`,
  );
}

export function anchorInterpolate(
  bankId: string,
  body: AnchorInterpolateParams,
): Promise<AnchorInterpolateResponse> {
  return sendJson(
    'POST',
    `/api/ops/anchor-interpolate?bank_id=${encodeURIComponent(bankId)}`,
    body,
  );
}


// ---- i3 endpoints --------------------------------------------------------

export function toneIdentifyOnly(
  bankId: string,
  body: ToneCorrectParams,
): Promise<ToneIdentifyOnlyResponse> {
  return sendJson(
    'POST',
    `/api/ops/tone-identify-only?bank_id=${encodeURIComponent(bankId)}`,
    body,
  );
}

export function toneIdentifyAndCorrect(
  bankId: string,
  body: ToneCorrectParams,
): Promise<ToneCorrectResponse> {
  return sendJson(
    'POST',
    `/api/ops/tone-identify-and-correct?bank_id=${encodeURIComponent(bankId)}`,
    body,
  );
}

export function splineTransfer(
  bankId: string,
  body: SplineTransferParams,
): Promise<SplineTransferResponse> {
  return sendJson(
    'POST',
    `/api/ops/spline-transfer?bank_id=${encodeURIComponent(bankId)}`,
    body,
  );
}

export function getMathAnalysis(bankId: string): Promise<MathAnalysisReport> {
  return getJson(`/api/banks/${encodeURIComponent(bankId)}/math-analysis`);
}

export function validateBankIntegrity(
  bankId: string,
  body: Record<string, unknown> = {},
): Promise<BankIntegrityResponse> {
  return sendJson(
    'POST',
    `/api/ops/bank-integrity-validate?bank_id=${encodeURIComponent(bankId)}`,
    body,
  );
}

export function buildExportUrl(
  bankId: string,
  format: ExportFormat,
  options: {excludeExtrapolated?: boolean} = {},
): string {
  const params = new URLSearchParams({format});
  if (options.excludeExtrapolated) params.set('exclude_extrapolated', 'true');
  return `/api/banks/${encodeURIComponent(bankId)}/export?${params.toString()}`;
}

export function getSurfaceGrid(
  bankId: string,
  options: {
    parameter: string;
    velocity: number;
    colorBy?: 'value' | 'fit_quality' | 'origin';
    differenceFrom?: string | null;
    kMax?: number;
  },
): Promise<SurfaceGrid> {
  const params = new URLSearchParams({
    parameter: options.parameter,
    velocity: String(options.velocity),
  });
  if (options.colorBy) params.set('color_by', options.colorBy);
  if (options.differenceFrom) params.set('difference_from', options.differenceFrom);
  if (options.kMax !== undefined) params.set('k_max', String(options.kMax));
  return getJson(
    `/api/banks/${encodeURIComponent(bankId)}/surface?${params.toString()}`,
  );
}

export function getPhysicalFit(
  bankId: string, midi: number, velocity: number,
): Promise<NoteMathDiag> {
  return getJson(
    `/api/banks/${encodeURIComponent(bankId)}/notes/${midi}/${velocity}/physical-fit`,
  );
}

export function getCrossNoteSeries(
  bankId: string, parameter: string, velocity: number, k: number,
): Promise<CrossNoteSeries> {
  return getJson(
    `/api/banks/${encodeURIComponent(bankId)}/cross-note/${encodeURIComponent(parameter)}/${velocity}/${k}`,
  );
}

export function getDeviationReport(
  bankId: string,
  options: {
    references: string[];
    minZ?: number;
    parameters?: string[];
  },
): Promise<DeviationReport> {
  const params: string[] = [];
  for (const r of options.references) {
    params.push(`ref=${encodeURIComponent(r)}`);
  }
  if (options.minZ !== undefined) params.push(`min_z=${options.minZ}`);
  for (const p of options.parameters ?? []) {
    params.push(`parameters=${encodeURIComponent(p)}`);
  }
  const qs = params.length > 0 ? `?${params.join('&')}` : '';
  return getJson(
    `/api/banks/${encodeURIComponent(bankId)}/deviation-report${qs}`,
  );
}


// ---- i6: MIDI bridge + ICR process ---------------------------------------

export function listMidiPorts(): Promise<import('@/types').MidiPorts> {
  return getJson('/api/midi/ports');
}

export function getMidiStatus(): Promise<import('@/types').MidiStatus> {
  return getJson('/api/midi/status');
}

export function connectMidi(body: {
  input_port_index: number | null;
  output_port_index: number | null;
}): Promise<import('@/types').MidiStatus> {
  return sendJson('POST', '/api/midi/connect', body);
}

export function disconnectMidi(): Promise<import('@/types').MidiStatus> {
  return sendJson('POST', '/api/midi/disconnect');
}

export function playNote(body: {
  midi: number;
  velocity?: number;
  duration_ms?: number;
  channel?: number;
}): Promise<unknown> {
  return sendJson('POST', '/api/midi/play-note', body);
}

export function midiPing(): Promise<import('@/types').MidiPingResponse> {
  return sendJson('POST', '/api/midi/ping');
}

export function pushBank(body: {
  bank_id: string;
  core?: import('@/types').IcrCore;
}): Promise<unknown> {
  return sendJson('POST', '/api/midi/push-bank', body);
}

export function pushPartial(body: {
  midi: number;
  velocity: number;
  k: number;
  parameter: string;
  value: number;
  core?: import('@/types').IcrCore;
}): Promise<unknown> {
  return sendJson('POST', '/api/midi/push-partial', body);
}

export function getIcrSettings(): Promise<import('@/types').IcrSettings> {
  return getJson('/api/icr/settings');
}

export function saveIcrSettings(body: {icr_path: string}): Promise<import('@/types').IcrSettings> {
  return sendJson('POST', '/api/icr/settings', body);
}

export function getIcrStatus(): Promise<import('@/types').IcrStatus> {
  return getJson('/api/icr/status');
}

export function launchIcr(body: {
  path?: string;
  extra_args?: string[];
}): Promise<import('@/types').IcrStatus> {
  return sendJson('POST', '/api/icr/launch', body);
}

export function stopIcr(): Promise<import('@/types').IcrStatus> {
  return sendJson('POST', '/api/icr/stop');
}
