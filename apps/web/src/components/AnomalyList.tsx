import {useMemo, useState} from 'react';
import {AlertTriangle, Search} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {ScrollArea} from '@/components/ui/scroll-area';
import {useBanks, useDeviationReport} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {DeviationEntry} from '@/types';

interface Props {
  bankId: string | null;
  onSelectAnomaly: (midi: number, velocity: number) => void;
  /** Gates the heavy deviation-report fetch. False on tabs that don't need it. */
  enabled?: boolean;
}

const ACTION_COLOR: Record<DeviationEntry['recommend_action'], string> = {
  review: 'bg-amber-100 text-amber-700',
  soft_correction: 'bg-orange-100 text-orange-700',
  hard_correction: 'bg-red-100 text-red-700',
  fill: 'bg-indigo-100 text-indigo-700',
};

export function AnomalyList({bankId, onSelectAnomaly, enabled = true}: Props) {
  const {data: banks} = useBanks();
  const [minZ, setMinZ] = useState(2.0);
  const [selectedRefs, setSelectedRefs] = useState<Record<string, boolean>>({});

  // All other banks become candidate references by default
  const allReferenceIds = useMemo(
    () => (banks ?? []).map((b) => b.id).filter((id) => id !== bankId),
    [banks, bankId],
  );

  const referenceIds = useMemo(() => {
    const hasExplicit = Object.values(selectedRefs).some(Boolean);
    if (hasExplicit) {
      return allReferenceIds.filter((id) => selectedRefs[id]);
    }
    return allReferenceIds;
  }, [allReferenceIds, selectedRefs]);

  const {data: report, isLoading, error} = useDeviationReport(
    bankId,
    referenceIds.length > 0 ? {references: referenceIds, minZ} : null,
    {enabled},
  );

  const toggleRef = (id: string) => {
    setSelectedRefs((prev) => ({...prev, [id]: !prev[id]}));
  };

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-600" />
          Anomalies
          {report && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              {report.n_entries} found
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {!bankId && <p className="text-xs text-zinc-400 italic">Pick a bank first.</p>}

        {bankId && (
          <>
            <div className="flex items-center gap-2 text-[11px]">
              <span className="w-14 text-zinc-500">min |z|</span>
              <input
                type="range"
                min={1}
                max={8}
                step={0.5}
                value={minZ}
                onChange={(e) => setMinZ(parseFloat(e.target.value))}
                className="flex-1"
              />
              <span className="w-8 text-right font-mono">{minZ.toFixed(1)}</span>
            </div>

            {allReferenceIds.length > 0 && (
              <div className="text-[10px] border border-zinc-100 rounded p-1.5 space-y-0.5 max-h-[80px] overflow-auto">
                <div className="font-semibold text-zinc-500 mb-0.5">
                  References ({referenceIds.length} active)
                </div>
                {allReferenceIds.map((id) => (
                  <label key={id} className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!!selectedRefs[id] || Object.values(selectedRefs).every((v) => !v)}
                      onChange={() => toggleRef(id)}
                    />
                    <span className="font-mono truncate">{id}</span>
                  </label>
                ))}
              </div>
            )}

            {isLoading && <p className="text-xs text-zinc-400">Scanning...</p>}
            {error && <p className="text-xs text-red-600">{(error as Error).message}</p>}

            {report && report.n_entries === 0 && (
              <p className="text-xs text-zinc-400 italic">
                No anomalies at |z| ≥ {minZ.toFixed(1)} across selected references.
              </p>
            )}

            {report && report.n_entries > 0 && (
              <ScrollArea className="h-[240px] border border-zinc-100 rounded">
                <div className="divide-y divide-zinc-50">
                  {report.entries.map((e, idx) => (
                    <button
                      type="button"
                      key={`${e.midi}-${e.velocity}-${e.k}-${e.parameter}-${idx}`}
                      onClick={() => onSelectAnomaly(e.midi, e.velocity)}
                      className="w-full text-left px-2 py-1.5 text-[10px] font-mono hover:bg-blue-50/40 focus:outline-none focus:bg-blue-50/60"
                    >
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            'px-1 py-0.5 rounded text-[9px]',
                            ACTION_COLOR[e.recommend_action] ?? 'bg-zinc-100 text-zinc-600',
                          )}
                        >
                          {e.recommend_action.split('_')[0]}
                        </span>
                        <span className="font-semibold">
                          m{e.midi}/v{e.velocity} · k={e.k}
                        </span>
                        <span className="text-zinc-500">{e.parameter}</span>
                        <span className="ml-auto font-semibold text-red-700">
                          z={e.z_score.toFixed(1)}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[9px] text-zinc-500">
                        target={e.target_value.toPrecision(3)} vs ref={e.reference_value.toPrecision(3)} ± {e.reference_sigma.toPrecision(2)}
                      </div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            )}

            {report && report.loo && (
              <p className="text-[10px] text-zinc-500 italic flex items-center gap-1">
                <Search className="w-3 h-3" />
                Leave-one-out active: target excluded from its own consensus.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
