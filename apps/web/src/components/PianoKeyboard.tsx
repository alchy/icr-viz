import {useMemo} from 'react';

import {cn} from '@/lib/utils';

/**
 * Horizontal piano keyboard covering an arbitrary MIDI range.
 *
 * Preserves the visual design from the legacy app's anchor-selection area:
 * white keys as the baseline, black keys stacked on top, octaves separated
 * by a subtle C-label. Clicking any key emits the MIDI number; hovering shows
 * note names.
 *
 * For i1 this is a read-only picker. Future iterations may add:
 *  - anchor pins on specific keys
 *  - velocity stripes
 *  - range brushing
 */

interface Props {
  midiRange: [number, number];          // inclusive, e.g. [21, 108]
  availableMidi: Set<number>;           // which MIDI numbers actually exist in the bank
  selectedMidi: number | null;
  onSelect: (midi: number) => void;
  // When set, every key click also fires a MIDI note-on through the bridge.
  // Pass the currently selected velocity (0-7) so Selection + audition share state.
  onAudition?: (midi: number) => void;
}

const BLACK_KEY_OFFSETS: Record<number, number> = {
  // Index within an octave (0=C, 1=C#, ... 11=B) -> 1 if black key
  1: 1, 3: 1, 6: 1, 8: 1, 10: 1,
};

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

function midiToName(midi: number): string {
  const octave = Math.floor(midi / 12) - 1;
  return `${NOTE_NAMES[midi % 12]}${octave}`;
}

function isBlackKey(midi: number): boolean {
  return BLACK_KEY_OFFSETS[midi % 12] === 1;
}

export function PianoKeyboard({midiRange, availableMidi, selectedMidi, onSelect, onAudition}: Props) {
  const handleClick = (m: number) => {
    onSelect(m);
    onAudition?.(m);
  };
  const [mLo, mHi] = midiRange;

  // Build the white-key layout first — black keys overlay positions between them.
  const whiteKeys = useMemo(() => {
    const out: number[] = [];
    for (let m = mLo; m <= mHi; m++) {
      if (!isBlackKey(m)) out.push(m);
    }
    return out;
  }, [mLo, mHi]);

  const blackKeys = useMemo(() => {
    const out: Array<{midi: number; offsetRatio: number}> = [];
    for (let m = mLo; m <= mHi; m++) {
      if (!isBlackKey(m)) continue;
      // Find the index of the white key immediately to the left of this black key.
      // Position the black key so it straddles that white key's right edge.
      let idx = 0;
      for (let i = 0; i < whiteKeys.length; i++) {
        if (whiteKeys[i] === m - 1) {
          idx = i;
          break;
        }
      }
      out.push({midi: m, offsetRatio: idx + 1}); // offset in units of whiteKey-widths
    }
    return out;
  }, [mLo, mHi, whiteKeys]);

  const whiteCount = whiteKeys.length;

  return (
    <div className="w-full">
      <div
        className="relative bg-zinc-50 border border-zinc-200 rounded-md select-none"
        style={{aspectRatio: `${whiteCount * 0.5} / 1`, minHeight: 56}}
      >
        {/* White keys */}
        <div className="absolute inset-0 flex">
          {whiteKeys.map((m) => {
            const available = availableMidi.has(m);
            const selected = m === selectedMidi;
            const isC = m % 12 === 0;
            return (
              <button
                type="button"
                key={m}
                onClick={() => available && handleClick(m)}
                disabled={!available}
                title={`MIDI ${m} (${midiToName(m)})`}
                className={cn(
                  'flex-1 border-r border-zinc-200 last:border-r-0 relative transition-colors',
                  'flex flex-col justify-end items-center pb-1',
                  available ? 'bg-white hover:bg-blue-50 cursor-pointer' : 'bg-zinc-100 cursor-not-allowed',
                  selected && 'bg-blue-500 hover:bg-blue-500',
                )}
              >
                {isC && (
                  <span
                    className={cn(
                      'text-[9px] font-mono',
                      selected ? 'text-white' : 'text-zinc-400',
                    )}
                  >
                    {midiToName(m)}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Black keys */}
        <div className="absolute inset-0 pointer-events-none">
          {blackKeys.map(({midi, offsetRatio}) => {
            const available = availableMidi.has(midi);
            const selected = midi === selectedMidi;
            const widthPct = (100 / whiteCount) * 0.6;
            const leftPct = (offsetRatio / whiteCount) * 100 - widthPct / 2;
            return (
              <button
                type="button"
                key={midi}
                onClick={() => available && handleClick(midi)}
                disabled={!available}
                title={`MIDI ${midi} (${midiToName(midi)})`}
                className={cn(
                  'absolute top-0 h-[60%] rounded-b-sm transition-colors',
                  'pointer-events-auto',
                  available ? 'bg-zinc-900 hover:bg-zinc-700 cursor-pointer' : 'bg-zinc-400 cursor-not-allowed',
                  selected && 'bg-blue-700 hover:bg-blue-700',
                )}
                style={{left: `${leftPct}%`, width: `${widthPct}%`}}
              />
            );
          })}
        </div>
      </div>

      {selectedMidi !== null && (
        <div className="mt-1 text-[11px] text-zinc-500 font-mono">
          Selected: MIDI {selectedMidi} ({midiToName(selectedMidi)})
        </div>
      )}
    </div>
  );
}
