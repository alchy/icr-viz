import {useMemo, useState} from 'react';
import {LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer} from 'recharts';
import {BarChart3} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {useCurves} from '@/lib/queries';
import {cn} from '@/lib/utils';
import {LOG_Y_PARAMS, MATH_PARAMS, type MathParam} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
}

const PARAM_COLOR: Record<MathParam, string> = {
  tau1: '#3b82f6',
  tau2: '#6366f1',
  A0: '#ef4444',
  a1: '#22c55e',
  beat_hz: '#a855f7',
  f_coef: '#f59e0b',
};

export function ParameterPlot({bankId, midi, velocity}: Props) {
  const [visible, setVisible] = useState<Set<MathParam>>(
    () => new Set(['tau1', 'tau2', 'A0'] as const),
  );
  const {data, isLoading, error} = useCurves(bankId, midi, velocity, MATH_PARAMS);

  const toggle = (p: MathParam) => {
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-blue-600" />
          Partial parameters
          {midi !== null && velocity !== null && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              MIDI {midi} / vel {velocity}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {MATH_PARAMS.map((p) => {
            const active = visible.has(p);
            return (
              <button
                key={p}
                type="button"
                onClick={() => toggle(p)}
                className={cn(
                  'h-6 px-2 rounded text-[10px] font-mono border transition-colors',
                  active
                    ? 'text-white border-transparent'
                    : 'bg-zinc-50 border-zinc-200 text-zinc-500 hover:bg-zinc-100',
                )}
                style={active ? {backgroundColor: PARAM_COLOR[p]} : undefined}
              >
                {p}
              </button>
            );
          })}
        </div>

        {!bankId && <p className="text-xs text-zinc-400 italic">Select a bank and note.</p>}
        {bankId && (midi === null || velocity === null) && (
          <p className="text-xs text-zinc-400 italic">Pick a MIDI note and velocity.</p>
        )}
        {isLoading && <p className="text-xs text-zinc-400">Loading curves...</p>}
        {error && <p className="text-xs text-red-600">{(error as Error).message}</p>}
        {data && (
          <div className="space-y-3">
            {MATH_PARAMS.filter((p) => visible.has(p)).map((p) => (
              <SingleParamChart
                key={p}
                param={p}
                points={data.parameters[p] ?? []}
                color={PARAM_COLOR[p]}
              />
            ))}
            {visible.size === 0 && (
              <p className="text-xs text-zinc-400 italic">No parameter selected above.</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface ChartProps {
  param: MathParam;
  points: Array<{k: number; value: number; sigma: number | null; fit_quality: number; origin: string}>;
  color: string;
}

function SingleParamChart({param, points, color}: ChartProps) {
  const useLog = (LOG_Y_PARAMS as readonly string[]).includes(param);

  const plotData = useMemo(() => {
    // Recharts log scale rejects zero / negative — filter aggressively.
    if (!useLog) return points;
    return points.filter((p) => p.value > 0);
  }, [points, useLog]);

  if (plotData.length === 0) {
    return (
      <div className="text-[11px] text-zinc-400 italic border border-zinc-100 rounded p-2">
        {param}: no plottable points (all zero or negative in log-y mode)
      </div>
    );
  }

  return (
    <div>
      <div className="text-[11px] font-mono text-zinc-600 mb-1 flex items-center gap-2">
        <span style={{color}}>●</span>
        <span>{param}</span>
        <span className="text-[9px] text-zinc-400">
          {useLog ? 'log y' : 'linear y'} · {plotData.length} partials
        </span>
      </div>
      <div style={{width: '100%', height: 160}}>
        <ResponsiveContainer>
          <LineChart data={plotData} margin={{top: 5, right: 15, left: 0, bottom: 0}}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
            <XAxis
              dataKey="k"
              type="number"
              domain={['dataMin', 'dataMax']}
              stroke="#71717a"
              tick={{fontSize: 10, fontFamily: 'monospace'}}
            />
            <YAxis
              scale={useLog ? 'log' : 'linear'}
              domain={useLog ? ['auto', 'auto'] : ['auto', 'auto']}
              stroke="#71717a"
              tick={{fontSize: 10, fontFamily: 'monospace'}}
              width={50}
            />
            <Tooltip
              contentStyle={{
                fontSize: 11,
                fontFamily: 'monospace',
                background: '#ffffff',
                border: '1px solid #e4e4e7',
              }}
              formatter={(val: number, _name, item) => {
                const p = item.payload as typeof plotData[number];
                return [
                  `${val.toPrecision(4)} (q=${p.fit_quality.toFixed(2)}, ${p.origin})`,
                  param,
                ];
              }}
              labelFormatter={(k) => `k=${k}`}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={color}
              strokeWidth={1.5}
              dot={{r: 2, fill: color}}
              activeDot={{r: 4}}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
