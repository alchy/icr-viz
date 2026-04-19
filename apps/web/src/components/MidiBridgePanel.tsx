/**
 * MIDI bridge panel — "MIDI" tab.
 *
 * This tab is a preference editor, not a live connector:
 *   - ICR is not running yet (that's tab 3), so Connect/Disconnect here
 *     can't actually be tested against the other end.
 *   - Instead we "Save config" to YAML; the backend auto-opens these ports
 *     on startup (see app lifespan) so the pair is ready by tab 3.
 *
 * UI:
 *   - Port list is the UNION of backend input_ports ∪ output_ports. Windows
 *     MME sometimes lists each loopMIDI port in only one direction, but the
 *     port is bidirectional in principle, so we surface every name and let
 *     the open attempt decide.
 *   - Two checkbox columns (input, output) with mutex — picking port X for
 *     input disables it in output to prevent a self-loop.
 *   - Bridge status displayed read-only so the user sees whether the
 *     backend-auto-connect picked up the saved pair on this run.
 */

import {useEffect, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {Plug, RefreshCw, Save} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {
  getMidiStatus,
  listMidiPorts,
  saveAppSettings,
} from '@/lib/api';
import {cn} from '@/lib/utils';

interface Props {
  requiresBank: boolean;
}

export function MidiBridgePanel({requiresBank}: Props) {
  const qc = useQueryClient();
  const [inputName, setInputName] = useState<string | null>(null);
  const [outputName, setOutputName] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  const portsQ = useQuery({queryKey: ['midi-ports'], queryFn: listMidiPorts});
  const midiStatusQ = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 3000,
  });

  // Hydrate from currently-open bridge (after backend auto-connect) so the
  // user doesn't have to re-tick on every page load.
  useEffect(() => {
    const s = midiStatusQ.data;
    if (!s) return;
    if (inputName === null && s.input_port_name) setInputName(s.input_port_name);
    if (outputName === null && s.output_port_name) setOutputName(s.output_port_name);
  }, [midiStatusQ.data]);

  const saveMut = useMutation({
    mutationFn: saveAppSettings,
    onSuccess: () => {
      setSavedMsg('Saved — takes effect on next backend start.');
      qc.invalidateQueries({queryKey: ['midi-status']});
      setTimeout(() => setSavedMsg(null), 4000);
    },
  });

  const ports = portsQ.data;
  const midiStatus = midiStatusQ.data;
  const connected = !!midiStatus?.output_open;

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

  const canSave = inputName !== null || outputName !== null;

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Plug className="w-4 h-4 text-blue-600" />
          MIDI bridge preferences
          <span
            className={cn(
              'ml-2 w-2 h-2 rounded-full',
              connected ? 'bg-green-500' : 'bg-zinc-300',
            )}
          />
          <span className="text-[10px] font-normal text-zinc-500">
            {connected
              ? `live: ${midiStatus?.output_port_name ?? 'connected'}`
              : 'bridge closed (auto-opens on backend start)'}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3 space-y-3">
        <p className="text-[10px] text-zinc-500 leading-snug">
          {requiresBank && <>Bridge is independent of bank selection. </>}
          ICR isn't running yet (tab 3), so we can't round-trip a PONG from
          here. Save your preferred pair; the backend auto-opens them on the
          next start so tab 3 finds them ready.
        </p>

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

        <div className="flex gap-2 items-center">
          <button
            type="button"
            onClick={() => portsQ.refetch()}
            className="h-7 px-3 text-[11px] rounded border bg-zinc-50 border-zinc-200 hover:bg-zinc-100 inline-flex items-center gap-1"
          >
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
          <button
            type="button"
            onClick={() => saveMut.mutate({
              midi: {
                default_input: inputName,
                default_output: outputName,
              },
            })}
            disabled={!canSave || saveMut.isPending}
            className={cn(
              'h-7 px-3 text-[11px] rounded border inline-flex items-center gap-1',
              !canSave
                ? 'bg-zinc-200 border-zinc-200 text-zinc-400 cursor-not-allowed'
                : 'bg-blue-600 border-blue-600 text-white hover:bg-blue-700',
            )}
          >
            <Save className="w-3 h-3" /> Save config
          </button>
          {savedMsg && (
            <span className="text-[10px] text-green-700 ml-2">{savedMsg}</span>
          )}
          {saveMut.isError && (
            <span className="text-[10px] text-red-600 ml-2">
              {(saveMut.error as Error).message}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

interface ColumnProps {
  title: string;
  allNames: string[];
  availability: string[];
  selected: string | null;
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
