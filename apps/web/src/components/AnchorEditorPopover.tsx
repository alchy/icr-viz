import {useState} from 'react';
import {X, Plus} from 'lucide-react';

import {useCreateAnchor} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {MathParam} from '@/types';
import {MATH_PARAMS} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
  /** Initial k (selected partial). */
  defaultK: number;
  /** Initial value (current partial parameter value). Used as value prefill. */
  defaultValue: number;
  /** Initial parameter to anchor on. */
  defaultParameter: MathParam;
  onClose: () => void;
  onBankChange: (newBankId: string) => void;
}

export function AnchorEditorPopover({
  bankId,
  midi,
  velocity,
  defaultK,
  defaultValue,
  defaultParameter,
  onClose,
  onBankChange,
}: Props) {
  const create = useCreateAnchor();
  const [k, setK] = useState(defaultK);
  const [parameter, setParameter] = useState<MathParam>(defaultParameter);
  const [value, setValue] = useState(defaultValue);
  const [weight, setWeight] = useState(0.5);
  const [note, setNote] = useState('');

  const disabled = bankId === null || midi === null || velocity === null;

  const handleSubmit = async () => {
    if (disabled) return;
    try {
      const res = await create.mutateAsync({
        bankId: bankId!,
        midi: midi!,
        velocity: velocity!,
        body: {k, parameter, value, weight, note},
      });
      onBankChange(res.new_bank_id);
      onClose();
    } catch (err) {
      // Display error in-place instead of closing
      console.error('create anchor failed', err);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40">
      <div className="bg-white rounded-lg shadow-xl border border-zinc-200 w-[380px] p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold text-zinc-800">Add anchor</h3>
          <button type="button" onClick={onClose} className="text-zinc-400 hover:text-zinc-700">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-3 text-xs">
          <div className="grid grid-cols-2 gap-2">
            <LabeledField label="k">
              <input
                type="number"
                min={1}
                value={k}
                onChange={(e) => setK(parseInt(e.target.value) || 1)}
                className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-xs"
              />
            </LabeledField>
            <LabeledField label="parameter">
              <select
                value={parameter}
                onChange={(e) => setParameter(e.target.value as MathParam)}
                className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-xs"
              >
                {MATH_PARAMS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </LabeledField>
          </div>

          <LabeledField label="value">
            <input
              type="number"
              step="any"
              value={value}
              onChange={(e) => setValue(parseFloat(e.target.value) || 0)}
              className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-xs font-mono"
            />
          </LabeledField>

          <LabeledField label={`weight (${weight.toFixed(2)})`}>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={weight}
                onChange={(e) => setWeight(parseFloat(e.target.value))}
                className="flex-1"
              />
              <div className="flex gap-1 text-[9px] text-zinc-500">
                <span>soft</span>
                <span>·</span>
                <span>hard</span>
              </div>
            </div>
          </LabeledField>

          <LabeledField label="note">
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="optional — why this anchor?"
              className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-xs"
            />
          </LabeledField>

          {create.isError && (
            <p className="text-red-600 text-[11px]">
              {(create.error as Error).message}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="h-7 px-3 text-[11px] rounded border border-zinc-200 bg-zinc-50 hover:bg-zinc-100"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={disabled || create.isPending}
              className={cn(
                'h-7 px-3 text-[11px] rounded border inline-flex items-center gap-1',
                disabled || create.isPending
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                  : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
              )}
            >
              <Plus className="w-3 h-3" />
              {create.isPending ? 'Saving…' : 'Add anchor'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function LabeledField({label, children}: {label: string; children: React.ReactNode}) {
  return (
    <label className="block">
      <span className="block text-[10px] uppercase tracking-wide text-zinc-500 mb-0.5">
        {label}
      </span>
      {children}
    </label>
  );
}
