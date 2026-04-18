import {Info} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {useBank} from '@/lib/queries';

interface Props {
  bankId: string | null;
}

export function BankMetaPanel({bankId}: Props) {
  const {data, isLoading, error} = useBank(bankId);

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Info className="w-4 h-4 text-blue-600" />
          Bank metadata
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 text-xs space-y-1">
        {!bankId && <p className="text-zinc-400 italic">Select a bank above.</p>}
        {isLoading && <p className="text-zinc-400">Loading...</p>}
        {error && <p className="text-red-600">{(error as Error).message}</p>}
        {data && (
          <>
            <MetaRow label="ID" value={data.id} />
            <MetaRow label="Instrument" value={data.instrument ?? '(none)'} />
            <MetaRow label="Notes" value={`${data.n_notes}`} />
            <MetaRow
              label="MIDI range"
              value={data.midi_range ? `${data.midi_range[0]}..${data.midi_range[1]}` : '-'}
            />
            <MetaRow
              label="Velocities"
              value={data.velocities.length ? data.velocities.join(', ') : '-'}
            />
            <MetaRow label="k_max" value={data.k_max != null ? `${data.k_max}` : '-'} />
            <MetaRow label="Source" value={data.source ?? '-'} />
            <MetaRow label="Created" value={data.created_at ?? '-'} />
            {data.parent_id && <MetaRow label="Parent" value={data.parent_id} />}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function MetaRow({label, value}: {label: string; value: string}) {
  return (
    <div className="flex gap-2">
      <span className="text-zinc-500 w-20 shrink-0">{label}</span>
      <span className="text-zinc-800 font-mono text-[11px] break-all">{value}</span>
    </div>
  );
}
