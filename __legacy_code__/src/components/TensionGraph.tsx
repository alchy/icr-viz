import React, { useRef, useEffect, useMemo } from 'react';
import { Sample, SynthesisMode, AnalysisResult } from '../types';
import { computeTensionEdges } from '../lib/dimensionality';

interface TensionGraphProps {
  samples: Sample[];
  mode: SynthesisMode;
  results: AnalysisResult[];
  anchorRefs: { midi: number, bankId: string }[];
  width?: number;
  height?: number;
  onClickNode?: (midi: number, bankId: string) => void;
}

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  midi: number;
  bankId: string;
  bankName: string;
  isGood: boolean;
  isAnchor: boolean;
  deviation: number;
}

/**
 * Force-directed tension graph: nodes = samples, edges = k-NN similarity.
 * Clusters form naturally based on parameter similarity.
 */
export const TensionGraph: React.FC<TensionGraphProps> = ({
  samples, mode, results, anchorRefs, width = 500, height = 350, onClickNode
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const nodesRef = useRef<Node[]>([]);
  const edgesRef = useRef<{ source: number, target: number, distance: number }[]>([]);

  // Limit samples for performance
  const limitedSamples = useMemo(() => {
    if (samples.length <= 200) return samples;
    // For additive, take only vel=0
    const vel0 = samples.filter(s => (s.vel ?? 0) === 0);
    if (vel0.length > 0 && vel0.length <= 200) return vel0;
    // Take every Nth sample
    const step = Math.ceil(samples.length / 200);
    return samples.filter((_, i) => i % step === 0);
  }, [samples]);

  // Initialize nodes and edges
  useEffect(() => {
    const edges = computeTensionEdges(limitedSamples, mode, 400);
    edgesRef.current = edges;

    const nodes: Node[] = limitedSamples.map((s, i) => {
      const res = results.find(r => r.midi === s.midi && r.bankId === s.bankId);
      const isAnchor = anchorRefs.some(ref => ref.midi === s.midi && ref.bankId === s.bankId);
      // Initialize in a circle
      const angle = (i / limitedSamples.length) * 2 * Math.PI;
      const r = Math.min(width, height) * 0.35;
      return {
        x: width / 2 + r * Math.cos(angle) + (Math.random() - 0.5) * 20,
        y: height / 2 + r * Math.sin(angle) + (Math.random() - 0.5) * 20,
        vx: 0, vy: 0,
        midi: s.midi,
        bankId: s.bankId,
        bankName: s.bankName,
        isGood: res?.isGood ?? true,
        isAnchor,
        deviation: res?.deviation ?? 0,
      };
    });
    nodesRef.current = nodes;

    // Run force simulation
    let iteration = 0;
    const maxIter = 150;

    const simulate = () => {
      if (iteration >= maxIter) {
        draw();
        return;
      }
      iteration++;
      const alpha = 1 - iteration / maxIter;
      const nodes = nodesRef.current;
      const edges = edgesRef.current;

      // Repulsion (all pairs - simplified for performance)
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[j].x - nodes[i].x;
          const dy = nodes[j].y - nodes[i].y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = alpha * 800 / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          nodes[i].vx -= fx;
          nodes[i].vy -= fy;
          nodes[j].vx += fx;
          nodes[j].vy += fy;
        }
      }

      // Attraction (edges)
      for (const edge of edges) {
        const a = nodes[edge.source];
        const b = nodes[edge.target];
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const idealDist = 20 + edge.distance * 30;
        const force = alpha * (dist - idealDist) * 0.02;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      // Center gravity
      for (const n of nodes) {
        n.vx += (width / 2 - n.x) * 0.001 * alpha;
        n.vy += (height / 2 - n.y) * 0.001 * alpha;
      }

      // Apply velocity with damping
      for (const n of nodes) {
        n.vx *= 0.6;
        n.vy *= 0.6;
        n.x += n.vx;
        n.y += n.vy;
        // Boundary
        n.x = Math.max(15, Math.min(width - 15, n.x));
        n.y = Math.max(15, Math.min(height - 15, n.y));
      }

      draw();
      animRef.current = requestAnimationFrame(simulate);
    };

    animRef.current = requestAnimationFrame(simulate);

    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [limitedSamples, mode, results, anchorRefs, width, height]);

  const draw = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, width, height);

    const nodes = nodesRef.current;
    const edges = edgesRef.current;

    // Draw edges
    ctx.strokeStyle = 'rgba(0,0,0,0.04)';
    ctx.lineWidth = 0.5;
    for (const edge of edges) {
      const a = nodes[edge.source];
      const b = nodes[edge.target];
      if (!a || !b) continue;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    // Draw nodes
    for (const n of nodes) {
      const r = n.isAnchor ? 5 : 3;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      if (n.isAnchor) {
        ctx.fillStyle = '#10b981';
        ctx.strokeStyle = '#059669';
        ctx.lineWidth = 1.5;
        ctx.fill();
        ctx.stroke();
      } else if (n.isGood) {
        ctx.fillStyle = 'rgba(59,130,246,0.6)';
        ctx.fill();
      } else {
        ctx.fillStyle = 'rgba(239,68,68,0.7)';
        ctx.fill();
      }
    }
  };

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onClickNode) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const nodes = nodesRef.current;
    for (const n of nodes) {
      const dx = n.x - x;
      const dy = n.y - y;
      if (dx * dx + dy * dy < 64) {
        onClickNode(n.midi, n.bankId);
        return;
      }
    }
  };

  if (limitedSamples.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-400 text-xs italic">
        No data for tension graph
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height }}
      className="cursor-crosshair"
      onClick={handleClick}
    />
  );
};
