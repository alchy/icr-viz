/**
 * i1+i2 UI: bank + note browser (i1) plus anchor CRUD + preview/commit flow (i2).
 *
 * When an anchor mutation creates a new Bank version, the UI automatically
 * switches to the child bank so the user sees the updated state. The parent
 * bank stays in the dropdown — users can navigate back to compare.
 */

import {useEffect, useMemo, useState} from 'react';
import {Piano} from 'lucide-react';

import {AnchorEditorPopover} from '@/components/AnchorEditorPopover';
import {AnchorInterpolatePanel} from '@/components/AnchorInterpolatePanel';
import {AnchorList} from '@/components/AnchorList';
import {AnomalyList} from '@/components/AnomalyList';
import {BankMetaPanel} from '@/components/BankMetaPanel';
import {BankSwitcher} from '@/components/BankSwitcher';
import {ExportPanel} from '@/components/ExportPanel';
import {IcrEnginePanel} from '@/components/IcrEnginePanel';
import {IntegrityIssuesPanel} from '@/components/IntegrityIssuesPanel';
import {MathRelationshipsPanel} from '@/components/MathRelationshipsPanel';
import {NoteSelector} from '@/components/NoteSelector';
import {ParameterPlot} from '@/components/ParameterPlot';
import {PartialTable} from '@/components/PartialTable';
import {SplineTransferPanel} from '@/components/SplineTransferPanel';
import {SurfacePanel} from '@/components/SurfacePanel';
import {ToneCorrectPanel} from '@/components/ToneCorrectPanel';
import {useBank, useNote, useNotes} from '@/lib/queries';
import type {MathParam} from '@/types';

export default function App() {
  const [bankId, setBankId] = useState<string | null>(null);
  const [midi, setMidi] = useState<number | null>(null);
  const [velocity, setVelocity] = useState<number | null>(null);
  const [editingAnchor, setEditingAnchor] = useState<{
    k: number;
    parameter: MathParam;
    value: number;
  } | null>(null);

  const {data: bank} = useBank(bankId);
  const {data: noteIndex} = useNotes(bankId);
  const {data: noteDetail} = useNote(bankId, midi, velocity);

  // Auto-select MIDI 60 / velocity 5 when a bank is first loaded.
  useEffect(() => {
    if (!bank || !noteIndex || noteIndex.length === 0) return;
    const availableMidi = new Set(noteIndex.map((n) => n.midi));
    const preferredMidi = availableMidi.has(60) ? 60 : noteIndex[0].midi;
    const availableVels = noteIndex.filter((n) => n.midi === preferredMidi).map((n) => n.velocity);
    const preferredVel = availableVels.includes(5) ? 5 : availableVels[0] ?? 0;
    setMidi(preferredMidi);
    setVelocity(preferredVel);
  }, [bank?.id, noteIndex]);

  const selectedPartial = useMemo(() => {
    if (!noteDetail || !editingAnchor) return null;
    return noteDetail.partials.find((p) => p.k === editingAnchor.k) ?? null;
  }, [noteDetail, editingAnchor]);

  // Default anchor value prefill: current partial's value at k for the chosen parameter.
  const handleOpenAnchorEditor = (k: number = 1, parameter: MathParam = 'tau1') => {
    if (!noteDetail) {
      setEditingAnchor({k, parameter, value: 0});
      return;
    }
    const partial = noteDetail.partials.find((p) => p.k === k);
    const rawValue = partial ? (partial[parameter as keyof typeof partial] as number | undefined) : undefined;
    setEditingAnchor({
      k,
      parameter,
      value: typeof rawValue === 'number' ? rawValue : 0,
    });
  };

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="bg-white border-b border-zinc-200 px-6 py-3">
        <div className="flex items-center gap-2">
          <Piano className="w-5 h-5 text-blue-600" />
          <h1 className="text-sm font-bold text-zinc-800">ICR Piano Spectral Editor</h1>
          <span className="text-[10px] font-mono text-zinc-400 ml-2">i6 · ICR engine + MIDI bridge</span>
          <div className="ml-auto">
            <button
              type="button"
              onClick={() => handleOpenAnchorEditor()}
              disabled={!bankId || midi === null || velocity === null}
              className="h-7 px-3 text-[11px] rounded border border-blue-600 bg-blue-600 text-white hover:bg-blue-700 disabled:bg-zinc-200 disabled:border-zinc-200 disabled:text-zinc-400 disabled:cursor-not-allowed"
            >
              + Add anchor
            </button>
          </div>
        </div>
      </header>

      <main className="p-6 grid grid-cols-1 lg:grid-cols-12 gap-4">
        <aside className="lg:col-span-4 space-y-4">
          <BankSwitcher selectedBankId={bankId} onSelect={setBankId} />
          <BankMetaPanel bankId={bankId} />
          <NoteSelector
            bankId={bankId}
            selectedMidi={midi}
            selectedVelocity={velocity}
            onSelectMidi={setMidi}
            onSelectVelocity={setVelocity}
          />
          <AnchorList
            bankId={bankId}
            midi={midi}
            velocity={velocity}
            onBankChange={setBankId}
          />
          <AnomalyList
            bankId={bankId}
            onSelectAnomaly={(m, v) => {
              setMidi(m);
              setVelocity(v);
            }}
          />
          <IntegrityIssuesPanel
            bankId={bankId}
            onSelectIssue={(m, v) => {
              setMidi(m);
              setVelocity(v);
            }}
          />
          <ExportPanel bankId={bankId} />
          <IcrEnginePanel bankId={bankId} midi={midi} velocity={velocity} />
        </aside>

        <section className="lg:col-span-8 space-y-4">
          <ParameterPlot bankId={bankId} midi={midi} velocity={velocity} />
          <ToneCorrectPanel
            bankId={bankId}
            midi={midi}
            velocity={velocity}
            onBankChange={setBankId}
          />
          <AnchorInterpolatePanel
            bankId={bankId}
            midi={midi}
            velocity={velocity}
            onBankChange={setBankId}
          />
          <SplineTransferPanel
            bankId={bankId}
            midi={midi}
            velocity={velocity}
            onBankChange={setBankId}
          />
          <MathRelationshipsPanel
            bankId={bankId}
            onSelectNote={(m, v) => {
              setMidi(m);
              setVelocity(v);
            }}
          />
          <SurfacePanel bankId={bankId} />
          <PartialTable bankId={bankId} midi={midi} velocity={velocity} />
        </section>
      </main>

      {editingAnchor && bankId && midi !== null && velocity !== null && (
        <AnchorEditorPopover
          bankId={bankId}
          midi={midi}
          velocity={velocity}
          defaultK={editingAnchor.k}
          defaultParameter={editingAnchor.parameter}
          defaultValue={editingAnchor.value}
          onClose={() => setEditingAnchor(null)}
          onBankChange={setBankId}
        />
      )}
    </div>
  );
}
