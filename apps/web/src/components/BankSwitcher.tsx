import {Database} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {useBanks} from '@/lib/queries';

interface Props {
  selectedBankId: string | null;
  onSelect: (bankId: string) => void;
}

export function BankSwitcher({selectedBankId, onSelect}: Props) {
  const {data: banks, isLoading, error} = useBanks();

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Database className="w-4 h-4 text-blue-600" />
          Banks
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3">
        {isLoading && <p className="text-xs text-zinc-400">Loading...</p>}
        {error && (
          <p className="text-xs text-red-600">Failed to load banks: {(error as Error).message}</p>
        )}
        {banks && banks.length === 0 && (
          <p className="text-xs text-zinc-400 italic">
            No banks yet — run <code className="bg-zinc-100 px-1 rounded">python scripts/ingest_idea_banks.py</code>
          </p>
        )}
        {banks && banks.length > 0 && (
          <select
            className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
            value={selectedBankId ?? ''}
            onChange={(e) => onSelect(e.target.value)}
          >
            <option value="" disabled>
              Pick a bank...
            </option>
            {banks.map((b) => (
              <option key={b.id} value={b.id}>
                {b.instrument ? `${b.instrument} — ${b.id}` : b.id}
              </option>
            ))}
          </select>
        )}
      </CardContent>
    </Card>
  );
}
