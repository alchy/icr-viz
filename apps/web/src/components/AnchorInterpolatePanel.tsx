import {useState, useMemo} from 'react';
import {Sparkles} from 'lucide-react';
import {LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer} from 'recharts';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {useAnchorInterpolate} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {MathParam, ParameterCurveDiag} from '@/types';
import {LOG_Y_PARAMS, MATH_PARAMS} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
  onBankChange: (newBankId: string) => void;
}

const PARAM_COLOR: Record<MathParam, string> = {
  tau1: '#3b82f6',
  tau2: '#6366f1',
  A0: '#ef4444',
  a1: '#22c55e',
  beat_hz: '#a855f7',
  f_coef: '#f59e0b',
};

export function AnchorInterpolatePanel({bankId, midi, velocity, onBankChange}: Props) {
  const [selectedParams, setSelectedParams] = useState<MathParam[]>(['tau1']);
  const [priorWeight, setPriorWeight] = useState(0.3);
  const [preview, setPreview] = useState<ParameterCurveDiag[]>([]);
  const op = useAnchorInterpolate();

  const disabled = !bankId || midi === null || velocity === null || selectedParams.length === 0;

  const togglePar = (p: MathParam) => {
    setSelectedParams((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );
  };

  const handlePreview = async () => {
    if (disabled) return;
    try {
      const res = await op.mutateAsync({
        bankId: bankId!,
        body: {
          target_note_ids: [[midi!, velocity!]],
          parameters: selectedParams,
          prior_weight: priorWeight,
          commit: false,
          random_seed: 0,
        },
      });
      setPreview(res.per_parameter);
    } catch (err) {
      console.error('preview failed', err);
    }
  };

  const handleCommit = async () => {
    if (disabled) return;
    const res = await op.mutateAsync({
      bankId: bankId!,
      body: {
        target_note_ids: [[midi!, velocity!]],
        parameters: selectedParams,
        prior_weight: priorWeight,
        commit: true,
        random_seed: 0,
      },
    });
    if (res.new_bank_id) {
      onBankChange(res.new_bank_id);
    }
    setPreview(res.per_parameter);
  };

  const warnings = useMemo(
    () => preview.flatMap((d) => d.warnings.map((w) => `${d.parameter}: ${w}`)),
    [preview],
  );

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-blue-600" />
          Anchor interpolate
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
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

        <div className="flex items-center gap-2 text-[11px]">
          <span className="w-24 text-zinc-500">prior weight</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={priorWeight}
            onChange={(e) => setPriorWeight(parseFloat(e.target.value))}
            className="flex-1"
          />
          <span className="w-8 text-right font-mono">{priorWeight.toFixed(2)}</span>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={handlePreview}
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
            onClick={handleCommit}
            disabled={disabled || op.isPending || preview.length === 0}
            className={cn(
              'h-7 px-3 text-[11px] rounded border',
              disabled || op.isPending || preview.length === 0
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
            )}
          >
            Commit to new bank
          </button>
        </div>

        {op.isError && (
          <p className="text-red-600 text-[11px]">{(op.error as Error).message}</p>
        )}

        {preview.length > 0 && (
          <div className="space-y-3">
            {preview.map((diag) => (
              <div key={`${diag.parameter}-${diag.midi}-${diag.velocity}`} className="border border-zinc-100 rounded p-2">
                <div className="text-[11px] font-mono text-zinc-600 mb-1 flex items-center gap-2">
                  <span style={{color: PARAM_COLOR[diag.parameter]}}>●</span>
                  <span>{diag.parameter}</span>
                  <span className="text-[9px] text-zinc-400">
                    λ={diag.lambda_used.toExponential(2)} · {diag.used_pchip ? 'PCHIP fallback' : 'penalized'} · n={diag.n_observations} · anchors={diag.n_anchors_used}
                  </span>
                </div>
                <MiniCurve diag={diag} />
              </div>
            ))}
            {warnings.length > 0 && (
              <div className="border border-amber-200 bg-amber-50 rounded p-2 text-[10px]">
                <div className="font-semibold text-amber-700 mb-1">Warnings</div>
                <ul className="list-disc list-inside text-amber-800 space-y-0.5">
                  {warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MiniCurve({diag}: {diag: ParameterCurveDiag}) {
  const data = useMemo(
    () =>
      diag.k_grid.map((k, i) => ({
        k,
        value: diag.values[i],
        sigmaLow: diag.values[i] - diag.sigmas[i],
        sigmaHigh: diag.values[i] + diag.sigmas[i],
      })),
    [diag],
  );
  const useLog = (LOG_Y_PARAMS as readonly string[]).includes(diag.parameter);
  // Filter out non-positive values for log axis
  const plotData = useLog ? data.filter((d) => d.value > 0) : data;

  if (plotData.length === 0) {
    return (
      <p className="text-[10px] text-zinc-400 italic">
        no plottable points for {diag.parameter}
      </p>
    );
  }

  return (
    <div style={{width: '100%', height: 130}}>
      <ResponsiveContainer>
        <LineChart data={plotData} margin={{top: 4, right: 10, left: 0, bottom: 0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
          <XAxis
            dataKey="k"
            type="number"
            domain={['dataMin', 'dataMax']}
            stroke="#71717a"
            tick={{fontSize: 9, fontFamily: 'monospace'}}
          />
          <YAxis
            scale={useLog ? 'log' : 'linear'}
            domain={['auto', 'auto']}
            stroke="#71717a"
            tick={{fontSize: 9, fontFamily: 'monospace'}}
            width={45}
          />
          <Tooltip
            contentStyle={{
              fontSize: 10,
              fontFamily: 'monospace',
              background: '#ffffff',
              border: '1px solid #e4e4e7',
            }}
            labelFormatter={(k) => `k=${k}`}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#3b82f6"
            strokeWidth={1.5}
            dot={{r: 1.5}}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
