import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { PhysicalParams } from '../types';

interface PhysicalVisualizerProps {
  params: PhysicalParams;
  predictedParams?: PhysicalParams;
  title?: string;
}

const PARAM_LABELS: Record<string, string> = {
  f0_hz: 'F0 (Hz)',
  B: 'Inharm (B)',
  gauge: 'Gauge',
  T60_fund: 'T60 Fund',
  T60_nyq: 'T60 Nyq',
  exc_x0: 'Exc x0',
  K_hardening: 'K Hard',
  p_hardening: 'P Hard',
  n_disp_stages: 'Disp Stg',
  disp_coeff: 'Disp Coef',
  n_strings: 'N Strings',
  detune_cents: 'Detune',
  hammer_mass: 'Ham Mass',
  string_mass: 'Str Mass',
  output_scale: 'Out Scale',
  bridge_refl: 'Bridge Refl',
};

export const PhysicalVisualizer: React.FC<PhysicalVisualizerProps> = ({ params, predictedParams, title }) => {
  const keys = Object.keys(params) as (keyof PhysicalParams)[];

  const data = keys.map(key => {
    const actual = params[key];
    const predicted = predictedParams?.[key];
    const maxVal = Math.max(Math.abs(actual), Math.abs(predicted ?? 0), 1e-9);
    return {
      name: PARAM_LABELS[key] || key,
      actual: actual / maxVal,
      predicted: predicted !== undefined ? predicted / maxVal : null,
      rawActual: actual,
      rawPredicted: predicted,
      diff: predicted !== undefined ? Math.abs((actual - predicted) / (Math.abs(predicted) || 1)) : 0,
    };
  });

  return (
    <div className="w-full h-[300px] flex flex-col">
      {title && <h3 className="text-[10px] font-bold mb-2 text-zinc-500 uppercase tracking-widest">{title}</h3>}
      <div className="flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 5, right: 20, left: 70, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="rgba(0,0,0,0.05)" />
            <XAxis
              type="number" domain={[-1.1, 1.1]}
              fontSize={10} tickLine={false} axisLine={false}
              tick={{ fill: 'currentColor', opacity: 0.5 }}
            />
            <YAxis
              type="category" dataKey="name"
              fontSize={9} tickLine={false} axisLine={false}
              width={65} tick={{ fill: 'currentColor', opacity: 0.6 }}
            />
            <Tooltip
              contentStyle={{ backgroundColor: '#fff', border: '1px solid #e4e4e7', fontSize: '11px', borderRadius: '8px', boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)' }}
              formatter={(_value: any, name: string, props: any) => {
                const entry = props.payload;
                if (name === 'actual') return [entry.rawActual?.toFixed(6), 'Actual'];
                if (name === 'predicted') return [entry.rawPredicted?.toFixed(6), 'Predicted'];
                return [_value, name];
              }}
            />
            <Bar dataKey="actual" fill="#3b82f6" radius={[0, 2, 2, 0]} barSize={8}>
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.diff > 0.15 ? '#ef4444' : '#3b82f6'} fillOpacity={0.85} />
              ))}
            </Bar>
            {predictedParams && (
              <Bar dataKey="predicted" fill="#10b981" radius={[0, 2, 2, 0]} fillOpacity={0.35} barSize={8} />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
