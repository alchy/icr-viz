/**
 * ICR runner panel — "Run ICR" tab.
 *
 * Prereqs (progressive lock): bank selected + MIDI bridge output_open.
 * Launch exports the bank to a temp JSON and starts icrgui with
 *   --core <EngineCore> --params <tempfile>
 * so the engine comes up with the bank already loaded.
 *
 * Once running: push-bank (live SysEx refresh after edits), play-note audition.
 */

import {useEffect, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {Cpu, Play, Settings, Square} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {
  getIcrSettings,
  getIcrStatus,
  getMidiStatus,
  getPushBankProgress,
  launchIcr,
  playNote,
  pushBank,
  saveIcrSettings,
  stopIcr,
} from '@/lib/api';
import {cn} from '@/lib/utils';
import type {IcrCore, IcrEngineCore} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
}

const ENGINE_CORES: IcrEngineCore[] = [
  'AdditiveCore',
  'PhysicalCore',
  'SamplerCore',
  'SineCore',
  'IFFSynthCore',
];

export function IcrRunnerPanel({bankId, midi, velocity}: Props) {
  const qc = useQueryClient();
  const [icrPath, setIcrPath] = useState('');
  const [engineCore, setEngineCore] = useState<IcrEngineCore>('AdditiveCore');
  const [sysexCore, setSysexCore] = useState<IcrCore>('active');

  const icrSettingsQ = useQuery({queryKey: ['icr-settings'], queryFn: getIcrSettings});
  const icrStatusQ = useQuery({
    queryKey: ['icr-status'],
    queryFn: getIcrStatus,
    refetchInterval: 3000,
  });
  const midiStatusQ = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (icrSettingsQ.data?.icr_path && !icrPath) {
      setIcrPath(icrSettingsQ.data.icr_path);
    }
  }, [icrSettingsQ.data]);

  const savePathMut = useMutation({
    mutationFn: saveIcrSettings,
    onSuccess: () => qc.invalidateQueries({queryKey: ['icr-settings']}),
  });
  const launchMut = useMutation({
    mutationFn: launchIcr,
    onSuccess: () => qc.invalidateQueries({queryKey: ['icr-status']}),
  });
  const stopMut = useMutation({
    mutationFn: stopIcr,
    onSuccess: () => qc.invalidateQueries({queryKey: ['icr-status']}),
  });
  const pushBankMut = useMutation({mutationFn: pushBank});
  const playNoteMut = useMutation({mutationFn: playNote});

  // Poll push-bank progress only while a job is in flight — saves the 200 ms
  // timer when idle and stops immediately on done/error.
  const pushProgressQ = useQuery({
    queryKey: ['push-bank-progress'],
    queryFn: getPushBankProgress,
    refetchInterval: pushBankMut.isPending ? 200 : false,
    enabled: pushBankMut.isPending || pushBankMut.isSuccess,
  });

  const icrStatus = icrStatusQ.data;
  const midiStatus = midiStatusQ.data;
  const midiConnected = !!midiStatus?.output_open;
  const running = !!icrStatus?.running;

  const missingReqs: string[] = [];
  if (!bankId) missingReqs.push('bank');
  if (!midiConnected) missingReqs.push('MIDI');
  if (!icrPath) missingReqs.push('icr path');
  const canLaunch = missingReqs.length === 0 && !running && !launchMut.isPending;

  // Preview the command exactly as the backend will invoke it. The actual
  // --soundbank-file path is resolved at launch time from the OS temp dir,
  // so we show the expected pattern. Windows paths get quoted when they
  // have spaces to match subprocess's own behaviour.
  const appSettingsQ = useQuery({
    queryKey: ['app-settings'],
    queryFn: async () => (await fetch('/api/settings')).json() as Promise<Record<string, unknown>>,
  });
  const quote = (s: string) => (s.includes(' ') ? `"${s}"` : s);
  const commandPreview = (() => {
    if (!icrPath) return null;
    const s = appSettingsQ.data ?? {};
    const parts: string[] = [quote(icrPath), '--core', engineCore];
    if (bankId) {
      parts.push('--soundbank-file');
      parts.push(quote(`<temp>/icr-viz-launch/${bankId}.icr.json`));
    }
    const sbDir = (s.soundbank_dir as string | null | undefined)
      ?? ((s.bank_dirs as string[] | undefined) ?? [])[0];
    if (sbDir) parts.push('--soundbank-dir', quote(sbDir));

    const irFile = s.ir_file as string | null | undefined;
    if (irFile) parts.push('--ir-file', quote(irFile));
    const irDir = s.ir_dir as string | null | undefined;
    if (irDir) parts.push('--ir-dir', quote(irDir));

    const ecFile = s.engine_config_file as string | null | undefined;
    if (ecFile) parts.push('--engine-config-file', quote(ecFile));
    const ecDir = s.engine_config_dir as string | null | undefined;
    if (ecDir) parts.push('--engine-config-dir', quote(ecDir));

    return parts.join(' ');
  })();

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Cpu className="w-4 h-4 text-blue-600" />
          ICR engine
          {running && (
            <span className="text-[9px] font-normal text-green-700 ml-2">
              running · pid {icrStatus.pid}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        {/* Engine path */}
        <div className="border border-zinc-100 rounded p-2 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 flex items-center gap-1">
            <Settings className="w-3 h-3" />
            Engine binary (icrgui.exe)
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={icrPath}
              onChange={(e) => setIcrPath(e.target.value)}
              placeholder="C:\path\to\icrgui.exe"
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
        </div>

        {/* Core + launch */}
        <div className="border border-zinc-100 rounded p-2 space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-zinc-500">
            Run with bank
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <span className="text-zinc-500 w-12 shrink-0">Core</span>
            <select
              value={engineCore}
              onChange={(e) => setEngineCore(e.target.value as IcrEngineCore)}
              className="bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[11px]"
              disabled={running}
            >
              {ENGINE_CORES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <span className="text-[10px] text-zinc-400 font-mono">
              --core {engineCore}
            </span>
          </div>
          <div className="text-[10px] text-zinc-500 font-mono">
            bank: {bankId ?? <span className="text-red-600">none</span>}
          </div>

          {/* MIDI config (read-only, set on tab 2) */}
          <div className="text-[10px] text-zinc-500 space-y-0.5">
            <div>
              MIDI in:{' '}
              <span className={cn(
                'font-mono',
                midiStatus?.input_port_name ? 'text-zinc-700' : 'text-red-600',
              )}>
                {midiStatus?.input_port_name ?? 'not set — configure on tab 2'}
              </span>
            </div>
            <div>
              MIDI out:{' '}
              <span className={cn(
                'font-mono',
                midiStatus?.output_port_name ? 'text-zinc-700' : 'text-red-600',
              )}>
                {midiStatus?.output_port_name ?? 'not set — configure on tab 2'}
              </span>
            </div>
          </div>

          {/* Command preview — exactly what the backend will spawn */}
          {commandPreview && (
            <div className="space-y-0.5">
              <div className="text-[10px] uppercase tracking-wide text-zinc-500">
                Command
              </div>
              <pre className="bg-zinc-900 text-zinc-100 rounded px-2 py-1.5 text-[10px] font-mono whitespace-pre-wrap break-all">
                {commandPreview}
              </pre>
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (!canLaunch || !bankId) return;
                launchMut.mutate({
                  path: icrPath || undefined,
                  bank_id: bankId,
                  core: engineCore,
                });
              }}
              disabled={!canLaunch}
              className={cn(
                'h-7 px-3 text-[11px] rounded border inline-flex items-center gap-1',
                !canLaunch
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                  : 'bg-green-600 border-green-600 text-white hover:bg-green-700',
              )}
              title={missingReqs.length ? `Missing: ${missingReqs.join(', ')}` : ''}
            >
              <Play className="w-3 h-3" /> Launch ICR
            </button>
            <button
              type="button"
              onClick={() => stopMut.mutate()}
              disabled={!running || stopMut.isPending}
              className={cn(
                'h-7 px-3 text-[11px] rounded border inline-flex items-center gap-1',
                !running
                  ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                  : 'bg-red-600 border-red-600 text-white hover:bg-red-700',
              )}
            >
              <Square className="w-3 h-3" /> Stop
            </button>
            {missingReqs.length > 0 && (
              <span className="text-[10px] text-amber-700 ml-auto self-center">
                needs: {missingReqs.join(', ')}
              </span>
            )}
          </div>
          {launchMut.isError && (
            <div className="text-[10px] text-red-600">
              {(launchMut.error as Error).message}
            </div>
          )}
        </div>

        {/* Live interaction — only useful while running */}
        {running && (
          <div className="border border-zinc-100 rounded p-2 space-y-1.5">
            <div className="text-[10px] uppercase tracking-wide text-zinc-500">
              Live (SysEx)
            </div>
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-zinc-500 w-12 shrink-0">SysEx core</span>
              <select
                value={sysexCore}
                onChange={(e) => setSysexCore(e.target.value as IcrCore)}
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
                onClick={() => {
                  if (bankId) pushBankMut.mutate({bank_id: bankId, core: sysexCore});
                }}
                disabled={!midiConnected || !bankId || pushBankMut.isPending}
                className={cn(
                  'h-7 px-2 text-[10px] rounded border',
                  !midiConnected || !bankId
                    ? 'bg-zinc-200 border-zinc-200 text-zinc-400'
                    : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
                )}
              >
                Push bank → engine
              </button>
              <button
                type="button"
                onClick={() => {
                  if (midi !== null && velocity !== null) {
                    playNoteMut.mutate({
                      midi,
                      velocity: Math.min(127, Math.max(1, velocity * 16 + 15)),
                      duration_ms: 800,
                    });
                  }
                }}
                disabled={!midiConnected || midi === null || velocity === null}
                className={cn(
                  'h-7 px-2 text-[10px] rounded border',
                  !midiConnected || midi === null
                    ? 'bg-zinc-200 border-zinc-200 text-zinc-400'
                    : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100',
                )}
              >
                Play m{midi ?? '?'} / v{velocity ?? '?'}
              </button>
            </div>
            {pushBankMut.isError && (
              <div className="text-[10px] text-red-600">
                {(pushBankMut.error as Error).message}
              </div>
            )}
            {(pushBankMut.isPending || pushProgressQ.data?.active) && (
              <PushBankProgress
                sent={pushProgressQ.data?.sent ?? 0}
                total={pushProgressQ.data?.total ?? 0}
                elapsedS={pushProgressQ.data?.elapsed_s ?? 0}
              />
            )}
            {pushBankMut.isSuccess && pushProgressQ.data?.done && !pushProgressQ.data?.error && (
              <div className="text-[10px] text-green-700">
                Sent {pushProgressQ.data.sent} / {pushProgressQ.data.total} frames
                {pushProgressQ.data.elapsed_s !== null && (
                  <> in {pushProgressQ.data.elapsed_s.toFixed(1)} s</>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PushBankProgress({
  sent, total, elapsedS,
}: {
  sent: number;
  total: number;
  elapsedS: number;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((sent / total) * 100)) : 0;
  const rate = elapsedS > 0.1 ? sent / elapsedS : 0;
  const etaS = rate > 0 && total > sent ? (total - sent) / rate : null;
  // If we're past a couple of seconds and the send rate dropped below 5
  // frames/s, the Windows MIDI driver is likely back-pressured. Warn the
  // user so they know to check whether the engine is actually consuming.
  const stalled = elapsedS > 3 && rate < 5 && sent < total;
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[10px] text-zinc-500">
        <span>
          Pushing bank…
          {stalled && (
            <span className="ml-2 text-amber-700">
              stalled — is icrgui receiving on loopMIDI?
            </span>
          )}
        </span>
        <span className="font-mono">
          {sent} / {total} ({pct}%) · {rate ? `${rate.toFixed(0)}/s` : '—'}
          {etaS !== null && <> · ETA {etaS.toFixed(0)} s</>}
        </span>
      </div>
      <div className="h-1.5 bg-zinc-200 rounded overflow-hidden">
        <div
          className={cn(
            'h-full transition-all',
            stalled ? 'bg-amber-500' : 'bg-blue-600',
          )}
          style={{width: `${pct}%`}}
        />
      </div>
    </div>
  );
}
