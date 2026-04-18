import {Pin, Trash2} from 'lucide-react';
import {useState} from 'react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {ScrollArea} from '@/components/ui/scroll-area';
import {useAnchors, useDeleteAnchor, useUpdateAnchor} from '@/lib/queries';
import {cn} from '@/lib/utils';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
  onBankChange: (newBankId: string) => void;
}

export function AnchorList({bankId, midi, velocity, onBankChange}: Props) {
  const {data: anchors, isLoading, error} = useAnchors(bankId, midi, velocity);
  const update = useUpdateAnchor();
  const del = useDeleteAnchor();
  const [editingId, setEditingId] = useState<string | null>(null);

  const handleWeightChange = async (anchorId: string, weight: number) => {
    if (!bankId) return;
    const res = await update.mutateAsync({
      bankId,
      anchorId,
      body: {weight},
    });
    onBankChange(res.new_bank_id);
  };

  const handleDelete = async (anchorId: string) => {
    if (!bankId) return;
    const res = await del.mutateAsync({bankId, anchorId});
    onBankChange(res.new_bank_id);
  };

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Pin className="w-4 h-4 text-blue-600" />
          Anchors
          {anchors && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              {anchors.length} pinned
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3">
        {(!bankId || midi === null || velocity === null) && (
          <p className="text-xs text-zinc-400 italic">Pick a note first.</p>
        )}
        {isLoading && <p className="text-xs text-zinc-400">Loading...</p>}
        {error && <p className="text-xs text-red-600">{(error as Error).message}</p>}
        {anchors && anchors.length === 0 && (
          <p className="text-xs text-zinc-400 italic">
            No anchors yet. Use the plot sidebar to create one.
          </p>
        )}
        {anchors && anchors.length > 0 && (
          <ScrollArea className="h-[260px]">
            <div className="space-y-1.5">
              {anchors.map((a) => (
                <div
                  key={a.id}
                  className={cn(
                    'border border-zinc-100 rounded p-2 text-[11px] font-mono',
                    editingId === a.id ? 'bg-blue-50/60 border-blue-200' : 'bg-zinc-50/50',
                  )}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold">
                      k={a.k} · {a.parameter}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleDelete(a.id)}
                      className="text-red-500 hover:text-red-700"
                      title="Delete anchor"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                  <div className="text-zinc-600">
                    value = {a.value.toPrecision(4)}
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="text-zinc-500 w-12 shrink-0">weight</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.01}
                      value={a.weight}
                      onChange={(e) => {
                        setEditingId(a.id);
                        handleWeightChange(a.id, parseFloat(e.target.value));
                      }}
                      className="flex-1"
                    />
                    <span className="w-8 text-right text-zinc-700">
                      {a.weight.toFixed(2)}
                    </span>
                  </div>
                  {a.note && (
                    <div className="mt-1 text-[10px] text-zinc-500 italic">{a.note}</div>
                  )}
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}
