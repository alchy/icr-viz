import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import { Harmonic } from '../types';

interface HarmonicVisualizerProps {
  harmonics: Harmonic[];
  predictedHarmonics?: Harmonic[];
  title?: string;
}

export const HarmonicVisualizer: React.FC<HarmonicVisualizerProps> = ({ harmonics, predictedHarmonics, title }) => {
  const data = harmonics.map((h, i) => {
    const pred = predictedHarmonics?.[i]?.amplitude;
    return {
      index: h.index,
      amplitude: h.amplitude,
      predicted: pred !== undefined ? pred : null,
      diff: pred !== undefined ? Math.abs(h.amplitude - pred) : 0
    };
  });

  return (
    <div className="w-full h-[300px] flex flex-col">
      {title && <h3 className="text-[10px] font-bold mb-2 text-zinc-500 uppercase tracking-widest">{title}</h3>}
      <div className="flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(0,0,0,0.05)" />
            <XAxis 
              dataKey="index" 
              fontSize={10} 
              tickLine={false} 
              axisLine={false}
              tick={{ fill: 'currentColor', opacity: 0.5 }}
            />
            <YAxis 
              fontSize={10} 
              tickLine={false} 
              axisLine={false}
              domain={[0, 1]}
              tick={{ fill: 'currentColor', opacity: 0.5 }}
            />
            <Tooltip 
              contentStyle={{ backgroundColor: '#fff', border: '1px solid #e4e4e7', fontSize: '12px', borderRadius: '8px', boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)' }}
              itemStyle={{ color: '#18181b' }}
            />
            <Bar dataKey="amplitude" fill="#3b82f6" radius={[2, 2, 0, 0]}>
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.diff > 0.1 ? '#ef4444' : '#3b82f6'} fillOpacity={0.8} />
              ))}
            </Bar>
            {predictedHarmonics && (
              <Bar dataKey="predicted" fill="#10b981" radius={[2, 2, 0, 0]} fillOpacity={0.3} />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
