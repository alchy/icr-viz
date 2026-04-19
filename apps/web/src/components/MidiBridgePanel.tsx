/**
 * MIDI bridge panel — "MIDI" tab.
 *
 * Design decisions:
 *   - Port list is the UNION of backend input_ports ∪ output_ports. Windows
 *     MME sometimes lists each loopMIDI port in only one direction, but a
 *     virtual port is bidirectional in principle, so we let the user try
 *     either assignment. The backend surfaces a clear error if the chosen
 *     name isn't actually available in the requested direction.
 *   - Selection is via two checkbox columns (input, output) with mutex —
 *     picking port X as input disables it in the output column and vice
 *     versa. This prevents routing the editor's own output back into its
 *     own input, which would create a feedback loop.
 *   - Bridge status is polled globally (3 s) so other tabs react without
 *     the user revisiting this tab.
 */

import {useEffect, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {Plug} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {
  connectMidi,
  disconnectMidi,
  getMidiStatus,
  listMidiPorts,
  midiPing,
} from '@/lib/api';
import {cn} from '@/lib/utils';

interface Props {
  requiresBank: boolean;
}

export function MidiBridgePanel({requiresBank}: Props) {
  const qc = useQueryClient();
  const [inputName, setInputName] = useState<string | null>(null);
  const [outputName, setOutputName] = useState<string | null>(null);
  const [pingRtt, setPingRtt] = useState<number | null>(null);

  const portsQ = useQuery({queryKey: ['midi-ports'], queryFn: listMidiPorts});
  const midiStatusQ = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 3000,
  });

  // Hydrate from server state on first mount so a browser reload shows the
  // current pair without requiring the user to re-pick.
  useEffect(() => {
    const s = midiStatusQ.data;
    if (!s) return;
    if (inputName === null && s.input_port_name) setInputName(s.input_port_name);
    if (outputName === null && s.output_port_name) setOutputName(s.output_port_name);
  }, [midiStatusQ.data]);

  const connectMut = useMutation({
    mutationFn: connectMidi,
    onSuccess: () => qc.invalidateQueries({queryKey: ['midi-status']}),
  });
  const disconnectMut = useMutation({
    mutationFn: disconnectMidi,
    onSuccess: () => qc.invalidateQueries({queryKey: ['midi-status']}),
  });
  const pingMut = useMutation({
    mutationFn: midiPing,
    onSuccess: (res) => setPingRtt(res.ok ? res.rtt_ms : null),
  });

  const ports = portsQ.data;
  const midiStatus = midiStatusQ.data;
  const connected = !!midiStatus?.output_open;

  // Union of all port names (dedup, stable order: inputs first, then output-only).
  const allNames: string[] = (() => {
    if (!ports) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const n of ports.input_ports) {
      if (!seen.has(n)) { seen.add(n); out.push(n); }
    }
    for (const n of ports.output_ports) {
      if (!seen.has(n)) { seen.add(n); out.push(n); }
    }
    return out;
  })();

  const connectable = inputName !== null || outputName !== null;

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Plug className="w-4 h-4 text-blue-600" />
          MIDI bridge
          <span
            className={cn(
              'ml-2 w-2 h-2 rounded-full',
              connected ? 'bg-green-500' : 'bg-zinc-300',
            )}
          />
          <span className="text-[10px] font-normal text-zinc-500">
            {connected ? midiStatus?.output_port_name ?? 'connected' : 'disconnected'}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {requiresBank && (
          <p className="text-[10px] text-zinc-500">
            MIDI bridge is independent of bank selection; downstream actions
            (Run ICR, Push bank, Audition) need both.
          </p>
        )}

        {allNames.length === 0 ? (
          <p className="text-[11px] text-zinc-500">
            No MIDI ports found. Start loopMIDI (or a virtual MIDI driver),
            then click Refresh.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <PortPickerColumn
              title="Input (engine PONG / feedback)"
              allNames={allNames}
              availability={ports?.input_ports ?? []}
              selected={inputName}
              disabledName={outputName}
              onPick={setInputName}
            />
            <PortPickerColumn
              title="Output (editor → engine)"
              allNames={allNames}
              availability={ports?.output_ports ?? []}
              selected={outputName}
              disabledName={inputName}
              onPick={setOutputName}
            />
          </div>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => connectMut.mutate({
              input_port_name: inputName,
              output_port_name: outputName,
            })}
            disabled={!connectable || connectMut.isPending}
            className={cn(
              'h-7 px-3 text-[11px] rounded border',
              !connectable
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
            )}
          >
            Connect
          </button>
          <button
            type="button"
            onClick={() => disconnectMut.mutate()}
            disabled={!connected}
            className={cn(
              'h-7 px-3 text-[11px] rounded border',
              !connected
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100',
            )}
          >
            Disconnect
          </button>
          <button
            type="button"
            onClick={() => portsQ.refetch()}
            className="h-7 px-2 text-[10px] rounded border bg-zinc-50 border-zinc-200 hover:bg-zinc-100"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => pingMut.mutate()}
            disabled={!connected || pingMut.isPending}
            className={cn(
              'h-7 px-2 text-[11px] rounded border ml-auto',
              !connected
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400'
                : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100',
            )}
          >
            Ping
            {pingRtt !== null && pingRtt !== undefined && (
              <span className="ml-1 text-green-700 font-mono">{pingRtt.toFixed(1)}ms</span>
            )}
            {pingMut.data && !pingMut.data.ok && (
              <span className="ml-1 text-red-600">timeout</span>
            )}
          </button>
        </div>

        {connectMut.isError && (
          <div className="text-[10px] text-red-600">
            {(connectMut.error as Error).message}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface ColumnProps {
  title: string;
  allNames: string[];
  /** Names that rtmidi confirmed are available in this direction — used to
   *  render a subtle hint when the user picks one that may not work. */
  availability: string[];
  selected: string | null;
  /** Name that's already chosen in the opposite column; disabled here. */
  disabledName: string | null;
  onPick: (name: string | null) => void;
}

function PortPickerColumn({
  title, allNames, availability, selected, disabledName, onPick,
}: ColumnProps) {
  const availSet = new Set(availability);
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">{title}</div>
      <ul className="border border-zinc-200 rounded divide-y divide-zinc-100">
        {allNames.map((name) => {
          const isChecked = selected === name;
          const isDisabled = disabledName === name;
          const isNative = availSet.has(name);
          return (
            <li key={name} className={cn('px-2 py-1', isDisabled && 'bg-zinc-50')}>
              <label
                className={cn(
                  'flex items-start gap-2 text-[11px] cursor-pointer',
                  isDisabled && 'cursor-not-allowed text-zinc-400',
                )}
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={isChecked}
                  disabled={isDisabled}
                  onChange={(e) => onPick(e.target.checked ? name : null)}
                />
                <span className="flex-1 font-mono">
                  {name}
                  {!isNative && (
                    <span className="ml-1 text-amber-600 text-[9px]">
                      (not native in this direction)
                    </span>
                  )}
                  {isDisabled && (
                    <span className="ml-1 text-zinc-400 text-[9px]">(used for other direction)</span>
                  )}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
