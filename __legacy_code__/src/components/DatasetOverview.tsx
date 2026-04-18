import React from 'react';
import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import { AnalysisResult } from '../types';
import { Badge } from '@/components/ui/badge';

interface DatasetOverviewProps {
  results: AnalysisResult[];
  onSelectMidi: (midi: number, bankId: string) => void;
  selectedMidi?: number;
  threshold: number;
}

export const DatasetOverview: React.FC<DatasetOverviewProps> = ({ results, onSelectMidi, selectedMidi, threshold }) => {
  if (results.length === 0) {
    return (
      <div className="w-full h-[400px] flex flex-col items-center justify-center text-zinc-400">
        <p className="text-xs uppercase tracking-widest font-bold">No analysis data</p>
        <p className="text-[10px] mt-1">Load soundbanks and set anchor points to begin</p>
      </div>
    );
  }

  const stats = {
    total: results.length,
    good: results.filter(r => r.isGood).length,
    bad: results.filter(r => !r.isGood).length,
    anchors: results.filter(r => r.isAnchor).length,
    avgDev: results.reduce((s, r) => s + r.deviation, 0) / results.length,
  };

  return (
    <div className="w-full h-[400px] flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Deviation Overview</h3>
        <div className="flex gap-2 text-[9px]">
          <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">{stats.anchors} anchors</Badge>
          <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">{stats.good} good</Badge>
          <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200">{stats.bad} anomalies</Badge>
          <Badge variant="outline" className="bg-zinc-50 text-zinc-600 border-zinc-200">avg: {stats.avgDev.toFixed(4)}</Badge>
        </div>
      </div>
      <div className="flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(0,0,0,0.05)" />
            <XAxis
              type="number" dataKey="midi" name="MIDI"
              domain={['auto', 'auto']}
              fontSize={10} tickLine={false} axisLine={false}
              tick={{ fill: 'currentColor', opacity: 0.5 }}
            />
            <YAxis
              type="number" dataKey="deviation" name="Deviation"
              fontSize={10} tickLine={false} axisLine={false}
              tick={{ fill: 'currentColor', opacity: 0.5 }}
            />
            <ZAxis type="number" range={[60, 60]} />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              content={({ active, payload }) => {
                if (active && payload && payload.length) {
                  const d = payload[0].payload as AnalysisResult;
                  return (
                    <div className="bg-white border border-zinc-200 p-3 rounded-lg shadow-xl text-xs">
                      <p className="font-bold text-zinc-900 mb-1">MIDI {d.midi}{d.vel !== undefined ? ` vel${d.vel}` : ''}</p>
                      <p className="text-zinc-500">Bank: <span className="text-blue-600 font-medium">{d.bankName}</span></p>
                      <p className="text-zinc-500">Deviation: <span className={d.isGood ? 'text-green-600 font-bold' : 'text-red-600 font-bold'}>{d.deviation.toFixed(6)}</span></p>
                      {d.zScore !== undefined && <p className="text-zinc-500">Z-score: <span className="font-mono">{d.zScore.toFixed(2)}</span></p>}
                      <div className="mt-2">
                        <Badge variant="outline" className={
                          d.isAnchor ? 'bg-green-50 text-green-700 border-green-200 text-[9px] font-bold'
                            : d.isGood ? 'bg-blue-50 text-blue-700 border-blue-200 text-[9px] font-bold'
                              : 'bg-red-50 text-red-700 border-red-200 text-[9px] font-bold'
                        }>
                          {d.isAnchor ? 'ANCHOR' : d.isGood ? 'STABLE' : 'ANOMALY'}
                        </Badge>
                      </div>
                    </div>
                  );
                }
                return null;
              }}
            />
            <Scatter name="Samples" data={results} onClick={(data: any) => onSelectMidi(data.midi, data.bankId)} className="cursor-pointer">
              {results.map((entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={entry.isAnchor ? '#10b981' : entry.isGood ? '#3b82f6' : '#ef4444'}
                  stroke={entry.midi === selectedMidi ? '#000' : 'none'}
                  strokeWidth={2}
                  fillOpacity={entry.isAnchor ? 0.9 : 0.7}
                />
              ))}
            </Scatter>
            <ReferenceLine y={threshold} stroke="#ef4444" strokeDasharray="3 3" label={{ position: 'right', value: `Threshold`, fill: '#ef4444', fontSize: 9, fontWeight: 'bold' }} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="flex gap-4 mt-3 text-[10px] uppercase tracking-widest font-bold text-zinc-400">
        <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-[#10b981]" /> Anchor</span>
        <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-[#3b82f6]" /> Good</span>
        <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-[#ef4444]" /> Anomaly</span>
      </div>
    </div>
  );
};
