import {useEffect, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {Cpu, Play, Square, Settings} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {
  connectMidi,
  disconnectMidi,
  getIcrSettings,
  getIcrStatus,
  getMidiStatus,
  launchIcr,
  listMidiPorts,
  midiPing,
  playNote,
  pushBank,
  saveIcrSettings,
  stopIcr,
} from '@/lib/api';
import {cn} from '@/lib/utils';
import type {IcrCore} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
}

/**
 * Combined ICR engine + MIDI bridge panel. "Editor a ICR jsou jedno" —
 * this card is the glue:
 *   - launches / stops icr.exe as a supervised subprocess
 *   - picks the MIDI port pair (editor output → engine input)
 *   - push-bank one-shot + play-note audition + ping health check
 */
export function IcrEnginePanel({bankId, midi, velocity}: Props) {
  const qc = useQueryClient();
  const [inputIdx, setInputIdx] = useState<number | null>(null);
  const [outputIdx, setOutputIdx] = useState<number | null>(null);
  const [core, setCore] = useState<IcrCore>('active');
  const [icrPath, setIcrPath] = useState('');
  const [pingRtt, setPingRtt] = useState<number | null>(null);

  const portsQ = useQuery({queryKey: ['midi-ports'], queryFn: listMidiPorts});
  const midiStatusQ = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 3000,
  });
  const icrSettingsQ = useQuery({queryKey: ['icr-settings'], queryFn: getIcrSettings});
  const icrStatusQ = useQuery({
    queryKey: ['icr-status'],
    queryFn: getIcrStatus,
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (icrSettingsQ.data?.icr_path && !icrPath) {
      setIcrPath(icrSettingsQ.data.icr_path);
    }
  }, [icrSettingsQ.data]);

  const connectMut = useMutation({
    mutationFn: connectMidi,
    onSuccess: () => qc.invalidateQueries({queryKey: ['midi-status']}),
  });
  const disconnectMut = useMutation({
    mutationFn: disconnectMidi,
    onSuccess: () => qc.invalidateQueries({queryKey: ['midi-status']}),
  });
  const launchMut = useMutation({
    mutationFn: launchIcr,
    onSuccess: () => qc.invalidateQueries({queryKey: ['icr-status']}),
  });
  const stopMut = useMutation({
    mutationFn: stopIcr,
    onSuccess: () => qc.invalidateQueries({queryKey: ['icr-status']}),
  });
  const savePathMut = useMutation({
    mutationFn: saveIcrSettings,
    onSuccess: () => qc.invalidateQueries({queryKey: ['icr-settings']}),
  });
  const pingMut = useMutation({
    mutationFn: midiPing,
    onSuccess: (res) => setPingRtt(res.ok ? res.rtt_ms : null),
  });
  const pushBankMut = useMutation({mutationFn: pushBank});
  const playNoteMut = useMutation({mutationFn: playNote});

  const ports = portsQ.data;
  const midiStatus = midiStatusQ.data;
  const icrStatus = icrStatusQ.data;
  const connected = !!midiStatus?.output_open;

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Cpu className="w-4 h-4 text-blue-600" />
          ICR engine &amp; MIDI
          {icrStatus?.running && (
            <span className="text-[9px] font-normal text-green-700 ml-2">
              running · pid {icrStatus.pid}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {/* Engine path + launch/stop */}
        <div className="border border-zinc-100 rounded p-2 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 flex items-center gap-1">
            <Settings className="w-3 h-3" />
            Engine
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={icrPath}
              onChange={(e) => setIcrPath(e.target.value)}
              placeholder="C:\path\to\icr.exe"
              className="flex-1 bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px] font-mono"
            />
            <button
              type="button"
              onClick={() => savePathMut.mutate({icr_path: icrPath})}
              disabled={!icrPath || savePathMut.isPending}
              className={cn(
                'h-7 px-2 text-[10px] rounded border',
                !icrPath || savePathMut.isPending
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400'
                  : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100',
              )}
            >
              Save path
            </button>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => launchMut.mutate({path: icrPath || undefined})}
              disabled={icrStatus?.running || !icrPath || launchMut.isPending}
              className={cn(
                'h-7 px-3 text-[11px] rounded border inline-flex items-center gap-1',
                icrStatus?.running || !icrPath
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                  : 'bg-green-600 border-green-600 text-white hover:bg-green-700',
              )}
            >
              <Play className="w-3 h-3" /> Launch
            </button>
            <button
              type="button"
              onClick={() => stopMut.mutate()}
              disabled={!icrStatus?.running || stopMut.isPending}
              className={cn(
                'h-7 px-3 text-[11px] rounded border inline-flex items-center gap-1',
                !icrStatus?.running
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                  : 'bg-red-600 border-red-600 text-white hover:bg-red-700',
              )}
            >
              <Square className="w-3 h-3" /> Stop
            </button>
          </div>
          {launchMut.isError && (
            <div className="text-[10px] text-red-600">
              {(launchMut.error as Error).message}
            </div>
          )}
        </div>

        {/* MIDI ports */}
        <div className="border border-zinc-100 rounded p-2 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">MIDI ports</div>

          <label className="block text-[11px]">
            <span className="block text-zinc-500 mb-0.5">Output (editor → engine)</span>
            <select
              className="w-full bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px]"
              value={outputIdx ?? ''}
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
              value={inputIdx ?? ''}
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
                input_port_index: inputIdx,
                output_port_index: outputIdx,
              })}
              disabled={outputIdx === null && inputIdx === null}
              className={cn(
                'h-7 px-3 text-[11px] rounded border',
                outputIdx === null && inputIdx === null
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
            <div className="flex items-center gap-1 ml-auto text-[10px]">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  connected ? 'bg-green-500' : 'bg-zinc-300',
                )}
              />
              {connected ? (midiStatus?.output_port_name ?? 'connected') : 'disconnected'}
            </div>
          </div>
        </div>

        {/* Live audition */}
        <div className="border border-zinc-100 rounded p-2 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">Audition</div>

          <div className="flex items-center gap-2 text-[11px]">
            <span className="text-zinc-500 w-12 shrink-0">Core</span>
            <select
              value={core}
              onChange={(e) => setCore(e.target.value as IcrCore)}
              className="bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px]"
            >
              <option value="active">active</option>
              <option value="additive">additive</option>
              <option value="physical">physical</option>
              <option value="sampler">sampler</option>
              <option value="sine">sine</option>
              <option value="iff">iff</option>
            </select>
          </div>

          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => pingMut.mutate()}
              disabled={!connected || pingMut.isPending}
              className={cn(
                'h-7 px-2 text-[10px] rounded border',
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

            <button
              type="button"
              onClick={() => {
                if (midi !== null && velocity !== null) {
                  playNoteMut.mutate({
                    midi, velocity: Math.min(127, Math.max(1, velocity * 16 + 15)),
                    duration_ms: 800,
                  });
                }
              }}
              disabled={!connected || midi === null || velocity === null}
              className={cn(
                'h-7 px-2 text-[10px] rounded border',
                !connected || midi === null
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400'
                  : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100',
              )}
            >
              Play m{midi ?? '?'} / v{velocity ?? '?'}
            </button>

            <button
              type="button"
              onClick={() => {
                if (bankId) pushBankMut.mutate({bank_id: bankId, core});
              }}
              disabled={!connected || !bankId || pushBankMut.isPending}
              className={cn(
                'h-7 px-2 text-[10px] rounded border',
                !connected || !bankId
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400'
                  : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
              )}
            >
              Push bank → engine
            </button>
          </div>

          {pushBankMut.isError && (
            <div className="text-[10px] text-red-600">
              {(pushBankMut.error as Error).message}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
