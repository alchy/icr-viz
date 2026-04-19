/**
 * TanStack Query hooks for piano_web API.
 *
 * Query keys match the hierarchy in `idea/i1.md §4.3` so they stay stable
 * across i1-i5. Anchor- and edit-related keys will be added in i2/i3 without
 * changing these read-only ones.
 */

import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';

import {
  anchorInterpolate,
  createAnchor,
  deleteAnchor,
  getBank,
  getCrossNoteSeries,
  getCurves,
  getDeviationReport,
  getMathAnalysis,
  getNote,
  getPhysicalFit,
  getSurfaceGrid,
  listAnchors,
  listBanks,
  listNotes,
  patchAnchor,
  splineTransfer,
  toneIdentifyAndCorrect,
  toneIdentifyOnly,
  validateBankIntegrity,
} from './api';
import type {
  AnchorCreate,
  AnchorInterpolateParams,
  AnchorPatch,
  SplineTransferParams,
  ToneCorrectParams,
} from '@/types';

export const queryKeys = {
  banks: () => ['banks'] as const,
  bank: (id: string) => ['bank', id] as const,
  notes: (id: string) => ['notes', id] as const,
  note: (id: string, midi: number, velocity: number) =>
    ['note', id, midi, velocity] as const,
  curves: (
    id: string,
    midi: number,
    velocity: number,
    parameters: readonly string[] | undefined,
  ) => ['curves', id, midi, velocity, parameters?.slice().sort().join(',') ?? ''] as const,
  anchors: (id: string, midi: number, velocity: number) =>
    ['anchors', id, midi, velocity] as const,
};

export function useBanks() {
  return useQuery({
    queryKey: queryKeys.banks(),
    queryFn: listBanks,
  });
}

export function useBank(bankId: string | null) {
  return useQuery({
    queryKey: queryKeys.bank(bankId ?? ''),
    queryFn: () => getBank(bankId!),
    enabled: !!bankId,
  });
}

export function useNotes(bankId: string | null) {
  return useQuery({
    queryKey: queryKeys.notes(bankId ?? ''),
    queryFn: () => listNotes(bankId!),
    enabled: !!bankId,
  });
}

export function useNote(
  bankId: string | null,
  midi: number | null,
  velocity: number | null,
) {
  const enabled = !!bankId && midi !== null && velocity !== null;
  return useQuery({
    queryKey: queryKeys.note(bankId ?? '', midi ?? -1, velocity ?? -1),
    queryFn: () => getNote(bankId!, midi!, velocity!),
    enabled,
  });
}

export function useCurves(
  bankId: string | null,
  midi: number | null,
  velocity: number | null,
  parameters?: readonly string[],
) {
  const enabled = !!bankId && midi !== null && velocity !== null;
  return useQuery({
    queryKey: queryKeys.curves(bankId ?? '', midi ?? -1, velocity ?? -1, parameters),
    queryFn: () => getCurves(bankId!, midi!, velocity!, parameters as string[] | undefined),
    enabled,
  });
}


// ---- i2 anchor hooks ---------------------------------------------------

export function useAnchors(
  bankId: string | null,
  midi: number | null,
  velocity: number | null,
) {
  const enabled = !!bankId && midi !== null && velocity !== null;
  return useQuery({
    queryKey: queryKeys.anchors(bankId ?? '', midi ?? -1, velocity ?? -1),
    queryFn: () => listAnchors(bankId!, midi!, velocity!),
    enabled,
  });
}

/**
 * Mutation hooks return the server's AnchorMutationResponse. Callers are
 * expected to lift `new_bank_id` from the response and switch the active
 * bank in app state — that is the convention enforced by the immutable
 * bank chain.
 */
export function useCreateAnchor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      bankId: string;
      midi: number;
      velocity: number;
      body: AnchorCreate;
    }) => createAnchor(input.bankId, input.midi, input.velocity, input.body),
    onSuccess: () => {
      qc.invalidateQueries({queryKey: ['banks']});
    },
  });
}

export function useUpdateAnchor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      bankId: string;
      anchorId: string;
      body: AnchorPatch;
    }) => patchAnchor(input.bankId, input.anchorId, input.body),
    onSuccess: () => {
      qc.invalidateQueries({queryKey: ['banks']});
    },
  });
}

export function useDeleteAnchor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {bankId: string; anchorId: string}) =>
      deleteAnchor(input.bankId, input.anchorId),
    onSuccess: () => {
      qc.invalidateQueries({queryKey: ['banks']});
    },
  });
}

export function useAnchorInterpolate() {
  return useMutation({
    mutationFn: (input: {bankId: string; body: AnchorInterpolateParams}) =>
      anchorInterpolate(input.bankId, input.body),
  });
}


// ---- i3 hooks -----------------------------------------------------------

export function useDeviationReport(
  bankId: string | null,
  options: {
    references: string[];
    minZ?: number;
    parameters?: string[];
  } | null,
  /**
   * Extra gate the caller AND-s with the built-in bank/options check.
   * Anchor-editing tabs pass `false` so the heavy recompute only fires
   * when the user actually opens the Analytics tab.
   */
  {enabled = true}: {enabled?: boolean} = {},
) {
  const fullyEnabled = enabled
    && !!bankId
    && !!options
    && options.references.length > 0;
  return useQuery({
    queryKey: ['deviation', bankId ?? '', options?.references.slice().sort().join(',') ?? '', options?.minZ ?? 2],
    queryFn: () => getDeviationReport(bankId!, options!),
    enabled: fullyEnabled,
    staleTime: 5 * 60 * 1000,
  });
}

export function useToneIdentifyOnly() {
  return useMutation({
    mutationFn: (input: {bankId: string; body: ToneCorrectParams}) =>
      toneIdentifyOnly(input.bankId, input.body),
  });
}

export function useToneIdentifyAndCorrect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {bankId: string; body: ToneCorrectParams}) =>
      toneIdentifyAndCorrect(input.bankId, input.body),
    onSuccess: () => {
      qc.invalidateQueries({queryKey: ['banks']});
    },
  });
}

export function useSplineTransfer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {bankId: string; body: SplineTransferParams}) =>
      splineTransfer(input.bankId, input.body),
    onSuccess: () => {
      qc.invalidateQueries({queryKey: ['banks']});
    },
  });
}


// ---- i4 math-analysis hooks --------------------------------------------

export function useMathAnalysis(
  bankId: string | null,
  {enabled = true}: {enabled?: boolean} = {},
) {
  return useQuery({
    queryKey: ['math-analysis', bankId ?? ''],
    queryFn: () => getMathAnalysis(bankId!),
    enabled: enabled && !!bankId,
    staleTime: 5 * 60 * 1000,
  });
}

export function usePhysicalFit(
  bankId: string | null, midi: number | null, velocity: number | null,
) {
  const enabled = !!bankId && midi !== null && velocity !== null;
  return useQuery({
    queryKey: ['physical-fit', bankId ?? '', midi ?? -1, velocity ?? -1],
    queryFn: () => getPhysicalFit(bankId!, midi!, velocity!),
    enabled,
  });
}

export function useCrossNoteSeries(
  bankId: string | null, parameter: string | null, velocity: number | null, k: number | null,
) {
  const enabled = !!bankId && !!parameter && velocity !== null && k !== null;
  return useQuery({
    queryKey: ['cross-note', bankId ?? '', parameter ?? '', velocity ?? -1, k ?? -1],
    queryFn: () => getCrossNoteSeries(bankId!, parameter!, velocity!, k!),
    enabled,
  });
}


// ---- i5 hooks ----------------------------------------------------------

export function useBankIntegrity(bankId: string | null) {
  return useMutation({
    mutationFn: (input: {bankId: string; body?: Record<string, unknown>}) =>
      validateBankIntegrity(input.bankId, input.body ?? {}),
  });
}

export function useSurfaceGrid(
  bankId: string | null,
  options: {
    parameter: string;
    velocity: number;
    colorBy?: 'value' | 'fit_quality' | 'origin';
    differenceFrom?: string | null;
  } | null,
) {
  const enabled = !!bankId && !!options;
  return useQuery({
    queryKey: [
      'surface',
      bankId ?? '',
      options?.parameter ?? '',
      options?.velocity ?? -1,
      options?.colorBy ?? 'value',
      options?.differenceFrom ?? '',
    ],
    queryFn: () => getSurfaceGrid(bankId!, options!),
    enabled,
  });
}
