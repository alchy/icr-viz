/**
 * MIDI bridge panel — just the port selection + connect/disconnect + ping.
 *
 * Lives on the "MIDI" tab. The "Connect" result is polled globally so any
 * other tab (Run ICR, Audition, Piano keyboard) can gate on `output_open`.
 */

import {useMemo, useState} from 'react';
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
  /** Disabled-state hint — won't block the UI, just communicates prerequisite. */
  requiresBank: boolean;
}

export function MidiBridgePanel({requiresBank}: Props) {
  const qc = useQueryClient();
  const [inputIdx, setInputIdx] = useState<number | null>(null);
  const [outputIdx, setOutputIdx] = useState<number | null>(null);
  const [pingRtt, setPingRtt] = useState<number | null>(null);

  const portsQ = useQuery({queryKey: ['midi-ports'], queryFn: listMidiPorts});
  const midiStatusQ = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 3000,
  });

  const ports = portsQ.data;
  const midiStatus = midiStatusQ.data;
  const connected = !!midiStatus?.output_open;

  // Best-effort: if server says we're connected and we don't have a local pick,
  // backfill from port names so the dropdowns don't look reset after a reload.
  const hydrated = useMemo(() => {
    if (!midiStatus || !ports) return {in: inputIdx, out: outputIdx};
    const resolve = (name: string | null, list: string[]) => {
      if (!name) return null;
      const i = list.findIndex((n) => n === name);
      return i >= 0 ? i : null;
    };
    return {
      in: inputIdx ?? resolve(midiStatus.input_port_name, ports.input_ports),
      out: outputIdx ?? resolve(midiStatus.output_port_name, ports.output_ports),
    };
  }, [midiStatus, ports, inputIdx, outputIdx]);

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
            MIDI bridge is independent of bank selection, but downstream actions
            (Run ICR, Push bank, Audition) need both.
          </p>
        )}

        <label className="block text-[11px]">
          <span className="block text-zinc-500 mb-0.5">Output (editor → engine)</span>
          <select
            className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px]"
            value={hydrated.out ?? ''}
            onChange={(e) => setOutputIdx(e.target.value === '' ? null : parseInt(e.target.value))}
          >
            <option value="">— select port —</option>
            {ports?.output_ports.map((name, i) => (
              <option key={i} value={i}>{i}: {name}</option>
            ))}
          </select>
        </label>

        <label className="block text-[11px]">
          <span className="block text-zinc-500 mb-0.5">Input (engine PONG / feedback)</span>
          <select
            className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px]"
            value={hydrated.in ?? ''}
            onChange={(e) => setInputIdx(e.target.value === '' ? null : parseInt(e.target.value))}
          >
            <option value="">— select port —</option>
            {ports?.input_ports.map((name, i) => (
              <option key={i} value={i}>{i}: {name}</option>
            ))}
          </select>
        </label>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => connectMut.mutate({
              input_port_index: hydrated.in,
              output_port_index: hydrated.out,
            })}
            disabled={hydrated.out === null && hydrated.in === null}
            className={cn(
              'h-7 px-3 text-[11px] rounded border',
              hydrated.out === null && hydrated.in === null
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
      </CardContent>
    </Card>
  );
}
