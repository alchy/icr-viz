import {useMemo, useState} from 'react';
import {ArrowRight} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {useBanks, useNotes, useSplineTransfer} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {MathParam, TransferMode} from '@/types';
import {MATH_PARAMS} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
  onBankChange: (newBankId: string) => void;
}

const MODES: TransferMode[] = ['absolute', 'relative', 'delta'];

const MODE_DESC: Record<TransferMode, string> = {
  absolute: 'target = source(k)',
  relative: 'target = target_k1 × source(k) / source(1)',
  delta: 'target = current + (source_smooth − source_raw)',
};

export function SplineTransferPanel({bankId, midi, velocity, onBankChange}: Props) {
  const {data: banks} = useBanks();
  const [sourceBankId, setSourceBankId] = useState<string>('');
  const [sourceMidi, setSourceMidi] = useState<number>(60);
  const [sourceVel, setSourceVel] = useState<number>(5);
  const [targetPars, setTargetPars] = useState<MathParam[]>(['tau1']);
  const [mode, setMode] = useState<TransferMode>('absolute');
  const [preserveFund, setPreserveFund] = useState(true);
  const [preview, setPreview] = useState<{changed: boolean; warnings: string[]} | null>(null);
  const op = useSplineTransfer();

  const {data: sourceNotes} = useNotes(sourceBankId || null);

  const availableSourceNotes = useMemo(
    () => (sourceNotes ?? []).filter((n) => n.velocity === sourceVel),
    [sourceNotes, sourceVel],
  );

  const availableVelocities = useMemo(
    () => Array.from(new Set((sourceNotes ?? []).map((n) => n.velocity))).sort(),
    [sourceNotes],
  );

  const disabled =
    !bankId || midi === null || velocity === null ||
    !sourceBankId || targetPars.length === 0;

  const toggleParam = (p: MathParam) =>
    setTargetPars((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );

  const runTransfer = async (commit: boolean) => {
    if (disabled) return;
    try {
      const res = await op.mutateAsync({
        bankId: bankId!,
        body: {
          source_bank_id: sourceBankId,
          source_note_id: [sourceMidi, sourceVel],
          target_note_ids: [[midi!, velocity!]],
          parameter_configs: targetPars.map((p) => ({
            parameter: p,
            mode,
            preserve_fundamental: preserveFund,
          })),
          commit,
        },
      });
      setPreview({changed: true, warnings: res.warnings});
      if (commit && res.new_bank_id) {
        onBankChange(res.new_bank_id);
      }
    } catch (err) {
      console.error('spline transfer failed', err);
    }
  };

  const otherBanks = (banks ?? []).filter((b) => b.id !== bankId);

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <ArrowRight className="w-4 h-4 text-blue-600" />
          Spline transfer
          <span className="text-[10px] font-normal text-zinc-500 ml-2">
            source curve → target note
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {/* Source bank + note */}
        <div className="border border-zinc-100 rounded p-2 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">Source</div>
          <select
            className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-xs"
            value={sourceBankId}
            onChange={(e) => setSourceBankId(e.target.value)}
          >
            <option value="">Pick a source bank...</option>
            {[...otherBanks, ...(banks?.filter((b) => b.id === bankId) ?? [])].map((b) => (
              <option key={b.id} value={b.id}>
                {b.instrument ? `${b.instrument} — ${b.id}` : b.id}
              </option>
            ))}
          </select>
          {sourceBankId && (
            <div className="flex gap-2 items-center text-[11px]">
              <span className="text-zinc-500 shrink-0">MIDI</span>
              <input
                type="number"
                min={21}
                max={108}
                value={sourceMidi}
                onChange={(e) => setSourceMidi(parseInt(e.target.value) || 0)}
                className="w-16 bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px] font-mono"
              />
              <span className="text-zinc-500 shrink-0">vel</span>
              <select
                value={sourceVel}
                onChange={(e) => setSourceVel(parseInt(e.target.value))}
                className="bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px] font-mono"
              >
                {availableVelocities.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
              {availableSourceNotes.length > 0 &&
                !availableSourceNotes.some((n) => n.midi === sourceMidi) && (
                  <span className="text-[9px] text-amber-600">
                    MIDI {sourceMidi} not in source
                  </span>
                )}
            </div>
          )}
        </div>

        {/* Target info (read-only — uses the main app selection) */}
        <div className="text-[11px] text-zinc-600">
          Target: {midi !== null ? `m${midi}/v${velocity}` : '(none)'}
        </div>

        {/* Parameter + mode */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">Parameters</div>
          <div className="flex flex-wrap gap-1.5">
            {MATH_PARAMS.map((p) => {
              const active = targetPars.includes(p);
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => toggleParam(p)}
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

        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">Mode</div>
          <div className="flex gap-1">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={cn(
                  'h-6 px-2 rounded text-[10px] font-mono border',
                  mode === m
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-zinc-50 border-zinc-200 text-zinc-500 hover:bg-zinc-100',
                )}
              >
                {m}
              </button>
            ))}
          </div>
          <p className="text-[10px] text-zinc-400 mt-0.5 font-mono">{MODE_DESC[mode]}</p>
        </div>

        <label className="flex items-center gap-2 text-[11px]">
          <input
            type="checkbox"
            checked={preserveFund}
            onChange={(e) => setPreserveFund(e.target.checked)}
          />
          <span>Preserve fundamental (k=1)</span>
        </label>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => runTransfer(false)}
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
            onClick={() => runTransfer(true)}
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
          <div className="text-[10px] space-y-1">
            <div className="text-green-700 font-semibold">Transfer prepared.</div>
            {preview.warnings.length > 0 && (
              <ul className="list-disc list-inside text-amber-700">
                {preview.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
