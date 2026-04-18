import {useState} from 'react';
import {Download} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {buildExportUrl} from '@/lib/api';
import {cn} from '@/lib/utils';
import type {ExportFormat} from '@/types';

interface Props {
  bankId: string | null;
}

const FORMAT_LABEL: Record<ExportFormat, string> = {
  icr: 'ICR v2 JSON',
  synth_csv: 'Synth CSV',
  analysis_csv: 'Analysis CSV',
  ndjson: 'NDJSON',
};

const FORMAT_DESC: Record<ExportFormat, string> = {
  icr: 'Full ICR v2 payload — anchors + partials, round-trippable',
  synth_csv: 'Flat per-partial rows for Pianoteq / custom synths',
  analysis_csv: 'Synth CSV + per-note fit diagnostics (B, α, γ, ...)',
  ndjson: 'Streaming newline-delimited JSON — one note per line',
};

export function ExportPanel({bankId}: Props) {
  const [format, setFormat] = useState<ExportFormat>('icr');
  const [excludeExtrapolated, setExcludeExtrapolated] = useState(false);

  const disabled = !bankId;
  const href = bankId
    ? buildExportUrl(bankId, format, {excludeExtrapolated})
    : '#';

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Download className="w-4 h-4 text-blue-600" />
          Export
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {!bankId && <p className="text-xs text-zinc-400 italic">Pick a bank first.</p>}

        {bankId && (
          <>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1">Format</div>
              <div className="flex flex-wrap gap-1.5">
                {(Object.keys(FORMAT_LABEL) as ExportFormat[]).map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setFormat(f)}
                    className={cn(
                      'h-6 px-2 rounded text-[10px] font-mono border',
                      format === f
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-zinc-50 border-zinc-200 text-zinc-500 hover:bg-zinc-100',
                    )}
                  >
                    {FORMAT_LABEL[f]}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-zinc-400 mt-1">{FORMAT_DESC[format]}</p>
            </div>

            <label className="flex items-center gap-2 text-[11px]">
              <input
                type="checkbox"
                checked={excludeExtrapolated}
                onChange={(e) => setExcludeExtrapolated(e.target.checked)}
              />
              <span>Exclude extrapolated partials</span>
            </label>

            <a
              href={href}
              download
              className={cn(
                'inline-flex items-center gap-1 h-7 px-3 text-[11px] rounded border',
                disabled
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400 pointer-events-none'
                  : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
              )}
            >
              <Download className="w-3 h-3" />
              Download {FORMAT_LABEL[format]}
            </a>
          </>
        )}
      </CardContent>
    </Card>
  );
}
