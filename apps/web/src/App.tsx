/**
 * ICR Piano Spectral Editor — tabbed shell.
 *
 * Workflow-ordered tabs with progressive unlock:
 *   1. Bank      — pick bank (always enabled)
 *   2. MIDI      — configure MIDI port pair (requires bank)
 *   3. Run ICR   — launch icrgui with selected bank + MIDI bridge
 *   4. Anchors   — keyboard + velocity + anchor CRUD + preview per note
 *   5. Analytics — tone-correct / spline-transfer / math-relationships / anomalies
 *   6. Surface   — midi × k heatmap (isolated, heavy to render)
 *
 * Shared state (bankId, midi, velocity, editing anchor) lives here so that
 * navigating between tabs preserves context.
 */

import {useEffect, useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Piano} from 'lucide-react';

import {AnchorEditorPopover} from '@/components/AnchorEditorPopover';
import {AnchorInterpolatePanel} from '@/components/AnchorInterpolatePanel';
import {AnchorList} from '@/components/AnchorList';
import {AnomalyList} from '@/components/AnomalyList';
import {BankMetaPanel} from '@/components/BankMetaPanel';
import {BankSwitcher} from '@/components/BankSwitcher';
import {ExportPanel} from '@/components/ExportPanel';
import {IcrRunnerPanel} from '@/components/IcrRunnerPanel';
import {IntegrityIssuesPanel} from '@/components/IntegrityIssuesPanel';
import {MathRelationshipsPanel} from '@/components/MathRelationshipsPanel';
import {MidiBridgePanel} from '@/components/MidiBridgePanel';
import {NoteSelector} from '@/components/NoteSelector';
import {ParameterPlot} from '@/components/ParameterPlot';
import {PartialTable} from '@/components/PartialTable';
import {SplineTransferPanel} from '@/components/SplineTransferPanel';
import {SurfacePanel} from '@/components/SurfacePanel';
import {ToneCorrectPanel} from '@/components/ToneCorrectPanel';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '@/components/ui/tabs';
import {getMidiStatus} from '@/lib/api';
import {useBank, useNote, useNotes} from '@/lib/queries';
import type {MathParam} from '@/types';

type TabId = 'bank' | 'midi' | 'run' | 'anchors' | 'analytics' | 'surface';

export default function App() {
  const [tab, setTab] = useState<TabId>('bank');
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

  // Poll MIDI status globally so tab gating reacts without the user touching
  // the MIDI tab after connecting.
  const midiStatusQ = useQuery({
    queryKey: ['midi-status'],
    queryFn: getMidiStatus,
    refetchInterval: 3000,
  });
  const midiReady = !!midiStatusQ.data?.output_open;

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

  const hasSelection = bankId && midi !== null && velocity !== null;

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="bg-white border-b border-zinc-200 px-6 py-3 sticky top-0 z-10">
        <div className="flex items-center gap-2">
          <Piano className="w-5 h-5 text-blue-600" />
          <h1 className="text-sm font-bold text-zinc-800">ICR Piano Spectral Editor</h1>
          <span className="text-[10px] font-mono text-zinc-400 ml-2">i6</span>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-[11px] text-zinc-500">
              {bankId ? <>bank <span className="font-mono text-zinc-700">{bankId}</span></> : 'no bank'}
              {midi !== null && velocity !== null && (
                <> · <span className="font-mono text-zinc-700">m{midi} v{velocity}</span></>
              )}
              {midiReady && <span className="ml-1 text-green-700">· MIDI</span>}
            </span>
            <button
              type="button"
              onClick={() => handleOpenAnchorEditor()}
              disabled={!hasSelection}
              className="h-7 px-3 text-[11px] rounded border border-blue-600 bg-blue-600 text-white hover:bg-blue-700 disabled:bg-zinc-200 disabled:border-zinc-200 disabled:text-zinc-400 disabled:cursor-not-allowed"
            >
              + Add anchor
            </button>
          </div>
        </div>
      </header>

      <Tabs value={tab} onValueChange={(v) => setTab(v as TabId)} className="px-6 pt-4">
        <TabsList className="w-fit mx-auto">
          <TabsTrigger value="bank">1 · Bank</TabsTrigger>
          <TabsTrigger value="midi" disabled={!bankId}>2 · MIDI</TabsTrigger>
          <TabsTrigger value="run" disabled={!bankId || !midiReady}>3 · Run ICR</TabsTrigger>
          <TabsTrigger value="anchors" disabled={!bankId}>4 · Anchors</TabsTrigger>
          <TabsTrigger value="analytics" disabled={!bankId}>5 · Analytics</TabsTrigger>
          <TabsTrigger value="surface" disabled={!bankId}>6 · Surface</TabsTrigger>
        </TabsList>

        {/* ───── Tab 1: Bank select ─────────────────────────────────────── */}
        <TabsContent value="bank" className="pb-8">
          <main className="pt-4 grid grid-cols-1 lg:grid-cols-12 gap-4">
            <aside className="lg:col-span-5 space-y-4">
              <BankSwitcher selectedBankId={bankId} onSelect={setBankId} />
              <ExportPanel bankId={bankId} />
            </aside>
            <section className="lg:col-span-7 space-y-4">
              <BankMetaPanel bankId={bankId} />
            </section>
          </main>
        </TabsContent>

        {/* ───── Tab 2: MIDI ports ──────────────────────────────────────── */}
        <TabsContent value="midi" className="pb-8">
          <main className="pt-4 max-w-2xl mx-auto">
            <MidiBridgePanel requiresBank={!bankId} />
          </main>
        </TabsContent>

        {/* ───── Tab 3: Run ICR engine ──────────────────────────────────── */}
        <TabsContent value="run" className="pb-8">
          <main className="pt-4 max-w-2xl mx-auto">
            <IcrRunnerPanel bankId={bankId} midi={midi} velocity={velocity} />
          </main>
        </TabsContent>

        {/* ───── Tab 4: Anchors — keyboard + edit workflow ──────────────── */}
        <TabsContent value="anchors" className="pb-8">
          <main className="pt-4 grid grid-cols-1 lg:grid-cols-12 gap-4">
            <aside className="lg:col-span-4 space-y-4">
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
            </aside>
            <section className="lg:col-span-8 space-y-4">
              <ParameterPlot bankId={bankId} midi={midi} velocity={velocity} />
              <PartialTable bankId={bankId} midi={midi} velocity={velocity} />
            </section>
          </main>
        </TabsContent>

        {/* ───── Tab 5: Analytics — heavy math + operator panels ────────── */}
        <TabsContent value="analytics" className="pb-8">
          <main className="pt-4 grid grid-cols-1 lg:grid-cols-12 gap-4">
            <aside className="lg:col-span-4 space-y-4">
              <NoteSelector
                bankId={bankId}
                selectedMidi={midi}
                selectedVelocity={velocity}
                onSelectMidi={setMidi}
                onSelectVelocity={setVelocity}
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
            </aside>
            <section className="lg:col-span-8 space-y-4">
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
            </section>
          </main>
        </TabsContent>

        {/* ───── Tab 6: Surface — heatmap only ──────────────────────────── */}
        <TabsContent value="surface" className="pb-8">
          <main className="pt-4">
            <SurfacePanel bankId={bankId} />
          </main>
        </TabsContent>
      </Tabs>

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
