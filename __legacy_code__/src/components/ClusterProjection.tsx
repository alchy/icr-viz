import React from 'react';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ZAxis } from 'recharts';
import { ProjectedPoint } from '../types';

interface ClusterProjectionProps {
  title: string;
  data: ProjectedPoint[];
  xLabel?: string;
  yLabel?: string;
  onClickPoint?: (point: ProjectedPoint) => void;
}

export const ClusterProjection: React.FC<ClusterProjectionProps> = ({
  title, data, xLabel = 'Component 1', yLabel = 'Component 2', onClickPoint
}) => {
  if (data.length === 0) {
    return (
      <div className="h-full flex flex-col">
        <h4 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-2">{title}</h4>
        <div className="flex-1 flex items-center justify-center text-zinc-400 text-xs italic">
          No data available
        </div>
      </div>
    );
  }

  const stats = {
    total: data.length,
    good: data.filter(d => d.isGood && !d.isAnchor).length,
    bad: data.filter(d => !d.isGood).length,
    anchors: data.filter(d => d.isAnchor).length,
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">{title}</h4>
        <div className="flex gap-3 text-[9px] text-zinc-400">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> {stats.anchors}
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" /> {stats.good}
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-500 inline-block" /> {stats.bad}
          </span>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 10, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.05)" />
            <XAxis
              type="number" dataKey="x" name={xLabel}
              fontSize={9} tickLine={false} axisLine={false}
              tick={{ fill: 'currentColor', opacity: 0.4 }}
              domain={['auto', 'auto']}
              label={{ value: xLabel, position: 'bottom', offset: 0, fontSize: 9, fill: '#a1a1aa' }}
            />
            <YAxis
              type="number" dataKey="y" name={yLabel}
              fontSize={9} tickLine={false} axisLine={false}
              tick={{ fill: 'currentColor', opacity: 0.4 }}
              domain={['auto', 'auto']}
              label={{ value: yLabel, angle: -90, position: 'insideLeft', fontSize: 9, fill: '#a1a1aa' }}
            />
            <ZAxis type="number" dataKey="deviation" range={[30, 200]} />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              content={({ active, payload }) => {
                if (active && payload && payload.length) {
                  const d = payload[0].payload as ProjectedPoint;
                  return (
                    <div className="bg-white border border-zinc-200 p-2 rounded-lg shadow-lg text-[10px]">
                      <p className="font-bold text-zinc-900">MIDI {d.midi}{d.vel !== undefined ? ` vel${d.vel}` : ''}</p>
                      <p className="text-zinc-500">{d.bankName}</p>
                      <p className="text-zinc-400">Dev: {d.deviation.toFixed(6)}</p>
                      <p className={d.isAnchor ? 'text-emerald-600 font-bold' : d.isGood ? 'text-blue-600' : 'text-red-600 font-bold'}>
                        {d.isAnchor ? 'ANCHOR' : d.isGood ? 'OK' : 'ANOMALY'}
                      </p>
                    </div>
                  );
                }
                return null;
              }}
            />
            <Scatter
              data={data}
              onClick={(data: any) => onClickPoint?.(data as ProjectedPoint)}
              className="cursor-pointer"
            >
              {data.map((entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={entry.isAnchor ? '#10b981' : entry.isGood ? '#3b82f6' : '#ef4444'}
                  fillOpacity={entry.isAnchor ? 0.9 : 0.6}
                  stroke={entry.isAnchor ? '#059669' : 'none'}
                  strokeWidth={entry.isAnchor ? 1.5 : 0}
                />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
