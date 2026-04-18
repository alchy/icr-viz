import {Table} from 'lucide-react';

import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {ScrollArea} from '@/components/ui/scroll-area';
import {useNote} from '@/lib/queries';
import {cn} from '@/lib/utils';
import type {Origin} from '@/types';

interface Props {
  bankId: string | null;
  midi: number | null;
  velocity: number | null;
}

const ORIGIN_COLOR: Record<Origin, string> = {
  measured: 'text-zinc-600 bg-zinc-100',
  derived: 'text-indigo-700 bg-indigo-100',
  extrapolated: 'text-amber-700 bg-amber-100',
  anchored: 'text-emerald-700 bg-emerald-100',
};

export function PartialTable({bankId, midi, velocity}: Props) {
  const {data, isLoading, error} = useNote(bankId, midi, velocity);

  return (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2 border-b border-zinc-50">
        <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
          <Table className="w-4 h-4 text-blue-600" />
          Partials
          {data && (
            <span className="text-[10px] font-normal text-zinc-500 ml-2">
              {data.partials.length} rows
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3">
        {!bankId || midi === null || velocity === null ? (
          <p className="text-xs text-zinc-400 italic">Pick a bank and note.</p>
        ) : isLoading ? (
          <p className="text-xs text-zinc-400">Loading...</p>
        ) : error ? (
          <p className="text-xs text-red-600">{(error as Error).message}</p>
        ) : data ? (
          <>
            <div className="text-[10px] text-zinc-500 font-mono mb-2 flex gap-3">
              <span>
                f<sub>0</sub> = {data.f0_hz.toFixed(3)} Hz
              </span>
              <span>B = {data.B.toExponential(3)}</span>
              <span>attack = {data.attack_tau.toFixed(3)} s</span>
            </div>
            <ScrollArea className="h-[320px] border border-zinc-100 rounded">
              <table className="w-full text-[10px] font-mono">
                <thead className="bg-zinc-50 text-zinc-500 sticky top-0">
                  <tr className="border-b border-zinc-200">
                    <Th>k</Th>
                    <Th right>f_hz</Th>
                    <Th right>A0</Th>
                    <Th right>tau1</Th>
                    <Th right>tau2</Th>
                    <Th right>a1</Th>
                    <Th right>beat_hz</Th>
                    <Th right>fit_q</Th>
                    <Th right>sigma</Th>
                    <Th>origin</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.partials.map((p) => (
                    <tr
                      key={p.k}
                      className={cn(
                        'border-b border-zinc-50 hover:bg-blue-50/40',
                        p.origin !== 'measured' && 'bg-zinc-50/60',
                      )}
                    >
                      <Td>{p.k}</Td>
                      <Td right>{p.f_hz.toFixed(2)}</Td>
                      <Td right>{p.A0.toPrecision(4)}</Td>
                      <Td right>{p.tau1.toPrecision(4)}</Td>
                      <Td right>{p.tau2.toPrecision(4)}</Td>
                      <Td right>{p.a1.toFixed(3)}</Td>
                      <Td right>{p.beat_hz.toFixed(3)}</Td>
                      <Td right>{p.fit_quality.toFixed(3)}</Td>
                      <Td right>{p.sigma == null ? '-' : p.sigma.toPrecision(3)}</Td>
                      <Td>
                        <span className={cn('px-1.5 py-0.5 rounded text-[9px]', ORIGIN_COLOR[p.origin])}>
                          {p.origin}
                        </span>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArea>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Th({children, right}: {children: React.ReactNode; right?: boolean}) {
  return (
    <th className={cn('px-2 py-1.5 font-semibold', right && 'text-right')}>{children}</th>
  );
}

function Td({children, right}: {children: React.ReactNode; right?: boolean}) {
  return <td className={cn('px-2 py-1', right && 'text-right')}>{children}</td>;
}
