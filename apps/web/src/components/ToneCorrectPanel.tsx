import {useMemo, useState} from 'react';
import {Wand2} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {ScrollArea} from '@/components/ui/scroll-area';
import {useBanks, useToneIdentifyAndCorrect} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {MathParam, ToneCorrectResponse} from '@/types';
import {MATH_PARAMS} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
  onBankChange: (newBankId: string) => void;
}

const ACTION_LABEL: Record<string, string> = {
  none: 'unchanged',
  fill: 'filled',
  soft_blend: 'blended',
  hard_replace: 'replaced',
};

const ACTION_COLOR: Record<string, string> = {
  none: 'text-zinc-500',
  fill: 'text-indigo-600',
  soft_blend: 'text-orange-600',
  hard_replace: 'text-red-600',
};

export function ToneCorrectPanel({bankId, midi, velocity, onBankChange}: Props) {
  const {data: banks} = useBanks();
  const [selectedRefs, setSelectedRefs] = useState<Record<string, boolean>>({});
  const [selectedParams, setSelectedParams] = useState<MathParam[]>(['tau1', 'tau2', 'A0']);
  const [preserveFundamental, setPreserveFundamental] = useState(true);
  const [noiseD, setNoiseD] = useState(1.0);
  const [correctionD, setCorrectionD] = useState(2.5);
  const [preview, setPreview] = useState<ToneCorrectResponse | null>(null);
  const op = useToneIdentifyAndCorrect();

  const referenceIds = useMemo(
    () =>
      (banks ?? [])
        .map((b) => b.id)
        .filter((id) => id !== bankId && selectedRefs[id]),
    [banks, bankId, selectedRefs],
  );

  const disabled = !bankId || midi === null || velocity === null || selectedParams.length === 0;

  const togglePar = (p: MathParam) =>
    setSelectedParams((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );

  const runPreview = async () => {
    if (disabled) return;
    const res = await op.mutateAsync({
      bankId: bankId!,
      body: {
        target_note_id: [midi!, velocity!],
        reference_bank_ids: referenceIds,
        parameters: selectedParams,
        preserve_fundamental: preserveFundamental,
        noise_threshold_d: noiseD,
        correction_threshold_d: correctionD,
        commit: false,
      },
    });
    setPreview(res);
  };

  const runCommit = async () => {
    if (disabled) return;
    const res = await op.mutateAsync({
      bankId: bankId!,
      body: {
        target_note_id: [midi!, velocity!],
        reference_bank_ids: referenceIds,
        parameters: selectedParams,
        preserve_fundamental: preserveFundamental,
        noise_threshold_d: noiseD,
        correction_threshold_d: correctionD,
        commit: true,
      },
    });
    setPreview(res);
    if (res.new_bank_id) onBankChange(res.new_bank_id);
  };

  const availableRefs = (banks ?? []).filter((b) => b.id !== bankId);

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Wand2 className="w-4 h-4 text-blue-600" />
          Identify &amp; Correct
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {/* Parameters */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">Parameters</div>
          <div className="flex flex-wrap gap-1.5">
            {MATH_PARAMS.map((p) => {
              const active = selectedParams.includes(p);
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => togglePar(p)}
                  className={cn(
                    'h-6 px-2 rounded text-[10px] font-mono border transition-colors',
                    active
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-zinc-50 border-zinc-200 text-zinc-500 hover:bg-zinc-100',
                  )}
                >
                  {p}
                </button>
              );
            })}
          </div>
        </div>

        {/* References */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">
            Reference banks ({referenceIds.length} selected)
          </div>
          {availableRefs.length === 0 ? (
            <p className="text-[10px] text-zinc-400 italic">No other banks available as references.</p>
          ) : (
            <div className="max-h-[80px] overflow-auto border border-zinc-100 rounded p-1.5 text-[10px] space-y-0.5">
              {availableRefs.map((b) => (
                <label key={b.id} className="flex items-center gap-1 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!selectedRefs[b.id]}
                    onChange={() => setSelectedRefs((prev) => ({...prev, [b.id]: !prev[b.id]}))}
                  />
                  <span className="font-mono truncate">{b.id}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        {/* Thresholds */}
        <div className="grid grid-cols-2 gap-2 text-[11px]">
          <ThresholdSlider label="noise d" value={noiseD} setValue={setNoiseD} min={0.2} max={3} step={0.1} />
          <ThresholdSlider label="correction d" value={correctionD} setValue={setCorrectionD} min={1} max={6} step={0.1} />
        </div>

        <label className="flex items-center gap-2 text-[11px]">
          <input
            type="checkbox"
            checked={preserveFundamental}
            onChange={(e) => setPreserveFundamental(e.target.checked)}
          />
          <span>Preserve fundamental (k=1)</span>
        </label>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={runPreview}
            disabled={disabled || op.isPending}
            className={cn(
              'h-7 px-3 text-[11px] rounded border',
              disabled || op.isPending
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100',
            )}
          >
            Preview
          </button>
          <button
            type="button"
            onClick={runCommit}
            disabled={disabled || op.isPending || !preview}
            className={cn(
              'h-7 px-3 text-[11px] rounded border',
              disabled || op.isPending || !preview
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
            )}
          >
            Commit to new bank
          </button>
        </div>

        {op.isError && (
          <p className="text-[11px] text-red-600">{(op.error as Error).message}</p>
        )}

        {preview && (
          <div className="border border-zinc-100 rounded p-2 space-y-1">
            <div className="text-[11px] font-mono flex gap-3 text-zinc-600">
              <span className="text-green-700 font-semibold">changed={preview.n_changed}</span>
              <span className="text-indigo-700 font-semibold">filled={preview.n_filled}</span>
              <span>unchanged={preview.n_unchanged}</span>
              {preview.new_bank_id && (
                <span className="ml-auto text-blue-700">→ {preview.new_bank_id}</span>
              )}
            </div>

            {preview.warnings.length > 0 && (
              <div className="text-[10px] border border-amber-200 bg-amber-50 rounded p-1.5">
                <div className="font-semibold text-amber-700">Warnings</div>
                <ul className="list-disc list-inside text-amber-800">
                  {preview.warnings.slice(0, 5).map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            <ScrollArea className="h-[180px] border border-zinc-50 rounded">
              <table className="w-full text-[10px] font-mono">
                <thead className="bg-zinc-50 text-zinc-500 sticky top-0">
                  <tr className="border-b border-zinc-100">
                    <th className="px-2 py-1 text-left">k</th>
                    <th className="px-2 py-1 text-left">param</th>
                    <th className="px-2 py-1 text-right">d</th>
                    <th className="px-2 py-1 text-right">current → new</th>
                    <th className="px-2 py-1 text-left">action</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.per_partial_log
                    .filter((e) => e.action !== 'none')
                    .slice(0, 100)
                    .map((e, i) => (
                      <tr key={i} className="border-b border-zinc-50">
                        <td className="px-2 py-0.5">{e.k}</td>
                        <td className="px-2 py-0.5">{e.parameter}</td>
                        <td className="px-2 py-0.5 text-right">
                          {e.normalized_deviation !== null ? e.normalized_deviation.toFixed(2) : '—'}
                        </td>
                        <td className="px-2 py-0.5 text-right">
                          {e.current !== null ? e.current.toPrecision(3) : '—'}
                          {' → '}
                          {e.new_value !== null ? e.new_value.toPrecision(3) : '—'}
                        </td>
                        <td className={cn('px-2 py-0.5', ACTION_COLOR[e.action])}>
                          {ACTION_LABEL[e.action] ?? e.action}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </ScrollArea>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ThresholdSlider({
  label, value, setValue, min, max, step,
}: {
  label: string;
  value: number;
  setValue: (v: number) => void;
  min: number;
  max: number;
  step: number;
}) {
  return (
    <label className="block">
      <span className="block text-zinc-500 mb-0.5">{label}</span>
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => setValue(parseFloat(e.target.value))}
          className="flex-1"
        />
        <span className="w-10 text-right font-mono">{value.toFixed(2)}</span>
      </div>
    </label>
  );
}
