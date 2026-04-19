import {useMemo} from 'react';
import {LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Scatter, ScatterChart} from 'recharts';
import {FlaskConical, AlertCircle, TrendingDown, TrendingUp} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {ScrollArea} from '@/components/ui/scroll-area';
import {useMathAnalysis} from '@/lib/queries';
import {cn} from '@/lib/utils';

interface Props {
  bankId: string | null;
  onSelectNote: (midi: number, velocity: number) => void;
  /** Gates the heavy math-analysis fetch. Set false on tabs that don't need it. */
  enabled?: boolean;
}

export function MathRelationshipsPanel({bankId, onSelectNote, enabled = true}: Props) {
  const {data, isLoading, error} = useMathAnalysis(bankId, {enabled});

  const B_series = useMemo(() => {
    if (!data) return [];
    return Object.values(data.per_note)
      .filter((d) => d.B_hat !== null && d.B_hat > 0)
      .map((d) => ({midi: d.midi, B_hat: d.B_hat as number}))
      .sort((a, b) => a.midi - b.midi);
  }, [data]);

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <FlaskConical className="w-4 h-4 text-indigo-600" />
          Math relationships
          {data && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              {data.n_notes} notes · {data.monotonicity_violations.length} violations · {data.outlier_notes.length} outliers
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {!bankId && <p className="text-xs text-zinc-400 italic">Pick a bank first.</p>}
        {isLoading && <p className="text-xs text-zinc-400">Running analysis...</p>}
        {error && <p className="text-xs text-red-600">{(error as Error).message}</p>}

        {data && (
          <div className="space-y-4">
            {/* B(midi) trend */}
            <TrendSection
              label="Inharmonicity B(midi)"
              stats={data.inharmonicity_trend}
              description="piano physics → B should rise with midi"
            >
              {B_series.length > 0 && (
                <div style={{width: '100%', height: 140}}>
                  <ResponsiveContainer>
                    <ScatterChart margin={{top: 5, right: 15, left: 0, bottom: 0}}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
                      <XAxis
                        dataKey="midi" type="number" domain={['dataMin', 'dataMax']}
                        stroke="#71717a" tick={{fontSize: 10, fontFamily: 'monospace'}}
                      />
                      <YAxis
                        dataKey="B_hat" scale="log" domain={['auto', 'auto']}
                        stroke="#71717a" tick={{fontSize: 10, fontFamily: 'monospace'}}
                        width={60}
                        tickFormatter={(v) => v.toExponential(1)}
                      />
                      <Tooltip
                        contentStyle={{fontSize: 10, fontFamily: 'monospace'}}
                        formatter={(v: number) => v.toExponential(3)}
                      />
                      <Scatter data={B_series} fill="#3b82f6" />
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              )}
            </TrendSection>

            {/* Power-law distributions */}
            <TrendSection
              label="τ₁ power-law exponent α"
              stats={data.tau1_power_trend}
              description="tau1 ∝ k^(-α) across all notes"
            />
            <TrendSection
              label="A₀ envelope β / μ"
              stats={data.A0_beta_distribution}
              description={`log A₀ = c − β log k − μ k · μ stats: median=${
                data.A0_mu_distribution.median?.toFixed(3) ?? '—'
              }`}
            />

            {/* Gamma ratio */}
            <GammaSection stats={data.gamma_ratio_stats} />

            {/* Monotonicity violations */}
            {data.monotonicity_violations.length > 0 && (
              <div className="border border-amber-200 bg-amber-50 rounded p-2">
                <div className="text-[11px] font-semibold text-amber-700 mb-1 flex items-center gap-1">
                  <TrendingUp className="w-3 h-3" />
                  Monotonicity violations ({data.monotonicity_violations.length})
                </div>
                <ScrollArea className="h-[120px]">
                  <div className="text-[10px] font-mono space-y-0.5">
                    {data.monotonicity_violations.slice(0, 30).map((v, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => onSelectNote(v.midi_to, v.velocity)}
                        className="flex gap-2 hover:bg-amber-100 w-full text-left px-1 py-0.5 rounded"
                      >
                        <span className={cn(
                          'font-semibold shrink-0',
                          v.severity === 'major' ? 'text-red-700' : 'text-amber-700',
                        )}>
                          {v.severity}
                        </span>
                        <span>
                          {v.parameter} k={v.k} · m{v.midi_from}→m{v.midi_to} v{v.velocity}
                        </span>
                        <span className="ml-auto text-zinc-500">Δ={v.delta.toPrecision(2)}</span>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            )}

            {/* Outliers */}
            {data.outlier_notes.length > 0 && (
              <div className="border border-red-200 bg-red-50 rounded p-2">
                <div className="text-[11px] font-semibold text-red-700 mb-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  Outlier notes ({data.outlier_notes.length})
                </div>
                <ScrollArea className="h-[120px]">
                  <div className="text-[10px] font-mono space-y-0.5">
                    {data.outlier_notes.slice(0, 30).map((o, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => onSelectNote(o.midi, o.velocity)}
                        className="flex gap-2 hover:bg-red-100 w-full text-left px-1 py-0.5 rounded"
                      >
                        <span className={cn(
                          'font-semibold shrink-0',
                          o.severity === 'strong' ? 'text-red-700' : 'text-orange-700',
                        )}>
                          z={o.mad_z.toFixed(1)}
                        </span>
                        <span>m{o.midi}/v{o.velocity}</span>
                        <span className="text-zinc-500">{o.attribute}</span>
                        <span className="ml-auto">
                          {o.value.toPrecision(3)} vs median {o.median.toPrecision(3)}
                        </span>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}


function TrendSection({
  label, stats, description, children,
}: {
  label: string;
  stats: {n: number; median: number | null; mean: number | null; iqr: [number, number] | null; r_squared: number | null};
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="border border-zinc-100 rounded p-2 space-y-1">
      <div className="text-[11px] font-semibold text-zinc-700">{label}</div>
      {description && <div className="text-[10px] text-zinc-400">{description}</div>}
      <div className="text-[10px] font-mono text-zinc-600 flex gap-3 flex-wrap">
        <span>n={stats.n}</span>
        {stats.median !== null && <span>median={stats.median.toPrecision(3)}</span>}
        {stats.mean !== null && <span>mean={stats.mean.toPrecision(3)}</span>}
        {stats.iqr && (
          <span>IQR=[{stats.iqr[0].toPrecision(3)}, {stats.iqr[1].toPrecision(3)}]</span>
        )}
        {stats.r_squared !== null && (
          <span className={stats.r_squared > 0.9 ? 'text-green-700' : 'text-amber-700'}>
            R²={stats.r_squared.toFixed(3)}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}


function GammaSection({stats}: {stats: {n: number; median: number | null; mean: number | null; min: number | null; max: number | null; p05: number | null; p95: number | null}}) {
  return (
    <div className="border border-zinc-100 rounded p-2 space-y-1">
      <div className="text-[11px] font-semibold text-zinc-700">γ = τ₂/τ₁</div>
      <div className="text-[10px] text-zinc-400">
        Published measurements: mean ≈ 2–4 typical for grand pianos
      </div>
      <div className="text-[10px] font-mono text-zinc-600 flex gap-3 flex-wrap">
        <span>n={stats.n}</span>
        {stats.median !== null && <span>median={stats.median.toPrecision(3)}</span>}
        {stats.mean !== null && <span>mean={stats.mean.toPrecision(3)}</span>}
        {stats.p05 !== null && stats.p95 !== null && (
          <span>5-95%=[{stats.p05.toPrecision(3)}, {stats.p95.toPrecision(3)}]</span>
        )}
      </div>
    </div>
  );
}
