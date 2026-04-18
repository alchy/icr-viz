import {useEffect} from 'react';
import {ShieldCheck, AlertTriangle} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {ScrollArea} from '@/components/ui/scroll-area';
import {useBankIntegrity} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {IntegrityIssue} from '@/types';

interface Props {
  bankId: string | null;
  onSelectIssue: (midi: number, velocity: number) => void;
}

const KIND_LABEL: Record<IntegrityIssue['kind'], string> = {
  monotonicity: 'Monotonicity',
  missing_note: 'Missing note',
  quality_floor: 'Low quality',
  physical_consistency: 'Physical',
  inharmonicity_range: 'B range',
  tau_ordering: 'τ ordering',
};

const SEVERITY_COLOR: Record<IntegrityIssue['severity'], string> = {
  info: 'bg-zinc-100 text-zinc-700',
  warning: 'bg-amber-100 text-amber-700',
  error: 'bg-red-100 text-red-700',
};

export function IntegrityIssuesPanel({bankId, onSelectIssue}: Props) {
  const op = useBankIntegrity(bankId);

  // Auto-run on bank change
  useEffect(() => {
    if (bankId) {
      op.mutate({bankId});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bankId]);

  const data = op.data;

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          {data?.ok ? (
            <ShieldCheck className="w-4 h-4 text-green-600" />
          ) : (
            <AlertTriangle className="w-4 h-4 text-amber-600" />
          )}
          Integrity
          {data && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              {data.n_errors} errors · {data.n_warnings} warnings
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-2">
        {!bankId && <p className="text-xs text-zinc-400 italic">Pick a bank first.</p>}
        {bankId && op.isPending && <p className="text-xs text-zinc-400">Validating...</p>}
        {op.isError && (
          <p className="text-xs text-red-600">{(op.error as Error).message}</p>
        )}

        {data && data.issues.length === 0 && (
          <p className="text-xs text-green-700 italic">All checks passed.</p>
        )}

        {data && data.issues.length > 0 && (
          <ScrollArea className="h-[200px] border border-zinc-100 rounded">
            <div className="divide-y divide-zinc-50">
              {data.issues.map((issue, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => onSelectIssue(issue.location.midi, issue.location.velocity)}
                  className="w-full text-left px-2 py-1.5 text-[10px] font-mono hover:bg-blue-50/40 focus:outline-none focus:bg-blue-50/60"
                >
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span
                      className={cn(
                        'px-1.5 py-0.5 rounded text-[9px]',
                        SEVERITY_COLOR[issue.severity],
                      )}
                    >
                      {KIND_LABEL[issue.kind] ?? issue.kind}
                    </span>
                    <span className="font-semibold">
                      m{issue.location.midi}/v{issue.location.velocity}
                      {issue.location.k !== null && ` · k=${issue.location.k}`}
                    </span>
                    {issue.location.parameter && (
                      <span className="text-zinc-500">{issue.location.parameter}</span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[9px] text-zinc-600">{issue.detail}</div>
                  {issue.suggested_operator && (
                    <div className="mt-0.5 text-[9px] text-blue-600">
                      → suggest: {issue.suggested_operator}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </ScrollArea>
        )}

        {bankId && !op.isPending && (
          <button
            type="button"
            onClick={() => op.mutate({bankId})}
            className="h-6 px-2 text-[10px] rounded border border-zinc-200 bg-zinc-50 hover:bg-zinc-100"
          >
            Re-check
          </button>
        )}
      </CardContent>
    </Card>
  );
}
