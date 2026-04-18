import {useMemo} from 'react';
import {useMutation, useQuery} from '@tanstack/react-query';
import {Music} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {getMidiStatus, playNote} from '@/lib/api';
import {useBank, useNotes} from '@/lib/queries';
import {cn} from '@/lib/utils';

import {PianoKeyboard} from './PianoKeyboard';

interface Props {
  bankId: string | null;
  selectedMidi: number | null;
  selectedVelocity: number | null;
  onSelectMidi: (midi: number) => void;
  onSelectVelocity: (velocity: number) => void;
}

export function NoteSelector({
  bankId,
  selectedMidi,
  selectedVelocity,
  onSelectMidi,
  onSelectVelocity,
}: Props) {
  const {data: bank} = useBank(bankId);
  const {data: noteIndex} = useNotes(bankId);

  // Poll bridge status so the keyboard only auditions when a real output is open.
  const {data: midiStatus} = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 5000,
    // Silently fail — the keyboard still works for selection without a bridge.
    retry: false,
  });
  const playMut = useMutation({mutationFn: playNote});

  const availableMidi = useMemo(
    () => new Set((noteIndex ?? []).map((n) => n.midi)),
    [noteIndex],
  );

  const midiRange = bank?.midi_range ?? null;
  const velocities = bank?.velocities ?? [];
  const bridgeOpen = !!midiStatus?.output_open;

  const handleAudition = (midiNote: number) => {
    if (!bridgeOpen) return;
    // Map velocity layer (0-7) to a meaningful MIDI velocity (1-127) — mid of each band.
    const vel = selectedVelocity !== null
      ? Math.min(127, Math.max(1, selectedVelocity * 16 + 15))
      : 90;
    playMut.mutate({midi: midiNote, velocity: vel, duration_ms: 600});
  };

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Music className="w-4 h-4 text-blue-600" />
          Note picker
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {!bankId && <p className="text-xs text-zinc-400 italic">Select a bank first.</p>}

        {bankId && midiRange && (
          <PianoKeyboard
            midiRange={midiRange}
            availableMidi={availableMidi}
            selectedMidi={selectedMidi}
            onSelect={onSelectMidi}
            onAudition={bridgeOpen ? handleAudition : undefined}
          />
        )}
        {bankId && midiRange && bridgeOpen && (
          <div className="text-[10px] text-green-700 font-mono flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-green-500" />
            MIDI audition live — click plays via {midiStatus?.output_port_name}
          </div>
        )}

        {bankId && velocities.length > 0 && (
          <div className="space-y-1">
            <div className="text-[11px] text-zinc-500 font-medium">Velocity</div>
            <div className="flex gap-1 flex-wrap">
              {velocities.map((v) => (
                <button
                  type="button"
                  key={v}
                  onClick={() => onSelectVelocity(v)}
                  className={cn(
                    'h-7 min-w-7 px-2 rounded text-[11px] font-mono border transition-colors',
                    selectedVelocity === v
                      ? 'bg-blue-600 border-blue-600 text-white'
                      : 'bg-zinc-50 border-zinc-200 text-zinc-700 hover:bg-zinc-100',
                  )}
                >
                  {v}
                </button>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
