import {useMemo, useState} from 'react';
import {Grid3x3} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {useBank, useSurfaceGrid} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {MathParam} from '@/types';

interface Props {
  bankId: string | null;
}

const PLOT_PARAMS: MathParam[] = ['tau1', 'tau2', 'A0', 'a1'];

export function SurfacePanel({bankId}: Props) {
  const {data: bank} = useBank(bankId);
  const [parameter, setParameter] = useState<MathParam>('tau1');
  const [velocity, setVelocity] = useState<number>(5);
  const [logScale, setLogScale] = useState(true);

  const {data: grid, isLoading} = useSurfaceGrid(bankId,
    bankId ? {parameter, velocity, colorBy: 'value'} : null,
  );

  // Auto-pick a valid velocity when bank changes
  const velocities = bank?.velocities ?? [];

  const flat = useMemo(() => {
    if (!grid) return {values: [] as number[], min: 0, max: 1};
    const all: number[] = [];
    for (const row of grid.z) {
      for (const v of row) {
        if (v !== null && isFinite(v) && (!logScale || v > 0)) all.push(v);
      }
    }
    if (all.length === 0) return {values: all, min: 0, max: 1};
    return {
      values: all,
      min: Math.min(...all),
      max: Math.max(...all),
    };
  }, [grid, logScale]);

  const colorFor = (v: number | null): string => {
    if (v === null || !isFinite(v)) return '#f4f4f5';
    if (flat.max <= flat.min) return '#93c5fd';
    let norm: number;
    if (logScale && flat.min > 0 && v > 0) {
      norm = (Math.log(v) - Math.log(flat.min)) / (Math.log(flat.max) - Math.log(flat.min));
    } else {
      norm = (v - flat.min) / (flat.max - flat.min);
    }
    norm = Math.max(0, Math.min(1, norm));
    // viridis-ish gradient: dark purple → blue → green → yellow
    const r = Math.round(68 + (253 - 68) * norm);
    const g = Math.round(1 + (231 - 1) * norm);
    const b = Math.round(84 + (37 - 84) * norm);
    return `rgb(${r},${g},${b})`;
  };

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Grid3x3 className="w-4 h-4 text-indigo-600" />
          Surface heatmap (midi × k)
          {grid && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              {grid.midi.length} × {grid.k.length} cells
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {!bankId && <p className="text-xs text-zinc-400 italic">Pick a bank first.</p>}

        {bankId && (
          <>
            <div className="flex flex-wrap gap-2 items-center text-[11px]">
              <div className="flex gap-1">
                {PLOT_PARAMS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setParameter(p)}
                    className={cn(
                      'h-6 px-2 rounded text-[10px] font-mono border',
                      parameter === p
                        ? 'bg-indigo-600 text-white border-indigo-600'
                        : 'bg-zinc-50 border-zinc-200 text-zinc-500 hover:bg-zinc-100',
                    )}
                  >
                    {p}
                  </button>
                ))}
              </div>

              {velocities.length > 0 && (
                <select
                  value={velocity}
                  onChange={(e) => setVelocity(parseInt(e.target.value))}
                  className="h-6 px-2 rounded text-[10px] font-mono border border-zinc-200 bg-zinc-50"
                >
                  {velocities.map((v) => (
                    <option key={v} value={v}>vel {v}</option>
                  ))}
                </select>
              )}

              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={logScale}
                  onChange={(e) => setLogScale(e.target.checked)}
                />
                <span>log color</span>
              </label>
            </div>

            {isLoading && <p className="text-xs text-zinc-400">Loading grid...</p>}

            {grid && grid.midi.length === 0 && (
              <p className="text-xs text-zinc-400 italic">
                No notes at velocity {velocity} in this bank.
              </p>
            )}

            {grid && grid.midi.length > 0 && (
              <div className="overflow-auto border border-zinc-100 rounded">
                <table className="text-[8px] font-mono border-collapse">
                  <thead>
                    <tr className="sticky top-0 bg-zinc-50">
                      <th className="px-1 py-0.5 text-zinc-500 border-b border-zinc-200">midi\k</th>
                      {grid.k.map((k) => (
                        <th key={k} className="px-1 py-0.5 text-zinc-500 border-b border-zinc-200">
                          {k}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {grid.midi.map((m, i) => (
                      <tr key={m}>
                        <th className="px-1 py-0.5 text-zinc-500 sticky left-0 bg-zinc-50 border-r border-zinc-200">
                          {m}
                        </th>
                        {grid.z[i].map((val, j) => (
                          <td
                            key={j}
                            className="border border-zinc-100"
                            style={{
                              background: colorFor(val),
                              width: 14,
                              height: 14,
                            }}
                            title={val !== null
                              ? `midi=${m}, k=${grid.k[j]}, ${parameter}=${val.toPrecision(4)}`
                              : `midi=${m}, k=${grid.k[j]}: no data`}
                          />
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="text-[10px] text-zinc-500 p-1 flex justify-between font-mono">
                  <span>min = {flat.min.toPrecision(3)}</span>
                  <span>max = {flat.max.toPrecision(3)}</span>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
