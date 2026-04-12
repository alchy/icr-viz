import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  Activity, Anchor, Download, Upload, Filter, RefreshCw, Settings2,
  Database, Cpu, Waves, FileText, Layers, BarChart3, GitBranch
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Checkbox } from '@/components/ui/checkbox';
import { Progress } from '@/components/ui/progress';
import { HarmonicVisualizer } from './components/HarmonicVisualizer';
import { PhysicalVisualizer } from './components/PhysicalVisualizer';
import { DatasetOverview } from './components/DatasetOverview';
import { ClusterProjection } from './components/ClusterProjection';
import { TensionGraph } from './components/TensionGraph';
import { SystemGuide } from './components/SystemGuide';
import {
  Soundbank, Sample, AnalysisResult, SoundbankMetadata, AnchorRef,
  SynthesisMode, LogEntry, CorrectionMethod, CorrectionConfig, ProjectedPoint, DetectionMethod
} from './types';
import { analyzeDataset, suggestAnchors, cleanDataset, applyCorrection, parseSoundbankJSON, generateCompleteBank } from './lib/analysis';
import { projectSamples, getProjectionParams, ProjectionMethod, ProjectionConfig } from './lib/dimensionality';
import _ from 'lodash';

const GITHUB_BASE_URL = "https://api.github.com/repos/alchy/ICR/contents";

type AppTab = 'physical' | 'additive' | 'docs';

export default function App() {
  // Core state
  const [mode, setMode] = useState<SynthesisMode>('physical');
  const [activeTab, setActiveTab] = useState<AppTab>('physical');
  const [availableBanks, setAvailableBanks] = useState<SoundbankMetadata[]>([]);
  const [selectedBankPaths, setSelectedBankPaths] = useState<string[]>([]);
  const [loadedBanks, setLoadedBanks] = useState<Soundbank[]>([]);
  const [loading, setLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [logs, setLogs] = useState<LogEntry[]>([]);

  // Analysis state
  const [anchorRefs, setAnchorRefs] = useState<AnchorRef[]>([]);
  const [activeBankPath, setActiveBankPath] = useState<string>("");
  const [threshold, setThreshold] = useState(0.05);
  const [detectionMethod, setDetectionMethod] = useState<DetectionMethod>('per-parameter');
  const [selectedMidi, setSelectedMidi] = useState<number>(60);
  const [selectedBankId, setSelectedBankId] = useState<string>("");
  const [isAutoMode, setIsAutoMode] = useState(false);
  const [manualMidi, setManualMidi] = useState("");

  // Correction state
  const [correctionMethod, setCorrectionMethod] = useState<CorrectionMethod>('threshold');
  const [zScoreLimit, setZScoreLimit] = useState(2.0);
  const [iqrMultiplier, setIqrMultiplier] = useState(1.5);
  const [isCorrected, setIsCorrected] = useState(false);
  const [correctedResults, setCorrectedResults] = useState<AnalysisResult[]>([]);
  const [correctedSamples, setCorrectedSamples] = useState<Sample[]>([]);

  // Projection state
  const [projectionMethod, setProjectionMethod] = useState<ProjectionMethod>('pca');
  const [projParamX, setProjParamX] = useState('f0_hz');
  const [projParamY, setProjParamY] = useState('T60_fund');

  // Logging
  const addLog = useCallback((message: string, level: LogEntry['level'] = 'info') => {
    setLogs(prev => [{
      timestamp: new Date().toLocaleTimeString(),
      level,
      message
    }, ...prev].slice(0, 200));
  }, []);

  // Mode change: reset
  useEffect(() => {
    setSelectedBankPaths([]);
    setLoadedBanks([]);
    setAnchorRefs([]);
    setSelectedBankId("");
    setActiveBankPath("");
    setIsAutoMode(false);
    setIsCorrected(false);
    setCorrectedResults([]);
    setCorrectedSamples([]);
    addLog(`Mode switched to ${mode}`, 'info');
    fetchBankList();
  }, [mode]);

  // Fetch bank list from GitHub
  const fetchBankList = async () => {
    try {
      setLoading(true);
      setStatusMessage(`Loading ${mode} bank list...`);
      const path = mode === 'additive' ? 'soundbanks-additive' : 'soundbanks-physical';
      const response = await fetch(`${GITHUB_BASE_URL}/${path}`);
      if (!response.ok) throw new Error(`GitHub API: ${response.status}`);
      const data = await response.json();

      const banks = data
        .filter((item: any) => item.name.endsWith('.json'))
        .map((item: any) => ({
          name: item.name.replace('.json', ''),
          path: item.path,
          download_url: item.download_url
        }));

      setAvailableBanks(banks);
      addLog(`Found ${banks.length} ${mode} banks in repo`, 'success');

      // Auto-select first bank
      if (banks.length > 0) {
        handleBankToggle(banks[0].path, banks);
      }
    } catch (err) {
      addLog(`Failed to fetch bank list: ${err}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  // Toggle bank selection
  const handleBankToggle = async (path: string, bankList?: SoundbankMetadata[]) => {
    const banks = bankList || availableBanks;

    if (selectedBankPaths.includes(path)) {
      setSelectedBankPaths(prev => prev.filter(p => p !== path));
      setLoadedBanks(prev => prev.filter(b => b.id !== path));
      if (activeBankPath === path) {
        const remaining = selectedBankPaths.filter(p => p !== path);
        setActiveBankPath(remaining.length > 0 ? remaining[0] : "");
      }
      setIsCorrected(false);
      return;
    }

    const bankMeta = banks.find(b => b.path === path);
    if (!bankMeta) return;

    try {
      setIsProcessing(true);
      setStatusMessage(`Loading ${bankMeta.name}...`);
      setProgress(10);

      const response = await fetch(bankMeta.download_url);
      setProgress(50);
      const data = await response.json();
      setProgress(80);

      // Use new parser
      const { samples, metadata, debug } = parseSoundbankJSON(data, bankMeta.path, bankMeta.name, mode);
      addLog(debug, samples.length > 0 ? 'debug' : 'error');

      if (samples.length === 0) {
        addLog(`Bank ${bankMeta.name}: no valid ${mode} samples found`, 'error');
        return;
      }

      setSelectedBankPaths(prev => [...prev, path]);
      setLoadedBanks(prev => [...prev, {
        id: bankMeta.path,
        name: bankMeta.name,
        mode,
        samples,
        metadata,
      }]);

      if (!selectedBankId) {
        setSelectedBankId(bankMeta.path);
        setSelectedMidi(samples[0].midi);
      }
      if (!activeBankPath) {
        setActiveBankPath(bankMeta.path);
      }

      setProgress(100);
      const midiRange = samples.map(s => s.midi);
      addLog(`${bankMeta.name}: ${samples.length} samples loaded, MIDI ${Math.min(...midiRange)}-${Math.max(...midiRange)}`, 'success');
    } catch (err) {
      addLog(`Failed to load ${bankMeta.name}: ${err}`, 'error');
    } finally {
      setIsProcessing(false);
      setIsCorrected(false);
    }
  };

  // Computed values
  const allSamples = useMemo(() => loadedBanks.flatMap(b => b.samples), [loadedBanks]);

  const analysisResults = useMemo(() => {
    if (allSamples.length === 0) return [];
    return analyzeDataset(allSamples, anchorRefs, threshold, mode, detectionMethod);
  }, [allSamples, anchorRefs, threshold, mode, detectionMethod]);

  const selectedResult = useMemo(() =>
    analysisResults.find(r => r.midi === selectedMidi && r.bankId === selectedBankId),
    [analysisResults, selectedMidi, selectedBankId]
  );

  const selectedSample = useMemo(() =>
    allSamples.find(s => s.midi === selectedMidi && s.bankId === selectedBankId),
    [allSamples, selectedMidi, selectedBankId]
  );

  // Projections (before correction)
  const projectionConfig: ProjectionConfig = useMemo(() => ({
    method: projectionMethod,
    paramX: projParamX,
    paramY: projParamY,
  }), [projectionMethod, projParamX, projParamY]);

  const projectedBefore = useMemo(() =>
    projectSamples(allSamples, mode, analysisResults, anchorRefs, projectionConfig),
    [allSamples, mode, analysisResults, anchorRefs, projectionConfig]
  );

  const projectedAfter = useMemo(() => {
    if (!isCorrected) return [];
    return projectSamples(correctedSamples, mode, correctedResults, anchorRefs, projectionConfig);
  }, [isCorrected, correctedSamples, mode, correctedResults, anchorRefs, projectionConfig]);

  const projParams = useMemo(() => getProjectionParams(mode), [mode]);

  // Actions
  const toggleAnchor = (midi: number, bankId: string) => {
    setAnchorRefs(prev => {
      const exists = prev.some(ref => ref.midi === midi && ref.bankId === bankId);
      return exists
        ? prev.filter(ref => !(ref.midi === midi && ref.bankId === bankId))
        : [...prev, { midi, bankId }];
    });
    setIsCorrected(false);
  };

  const handleAutoSelect = async () => {
    if (allSamples.length === 0) return;
    setIsProcessing(true);
    setStatusMessage("Auto-selecting anchors...");
    const suggested = suggestAnchors(allSamples, mode);
    setAnchorRefs(suggested);
    setIsAutoMode(true);
    setIsCorrected(false);
    addLog(`Auto-selected ${suggested.length} anchor points`, 'success');
    setIsProcessing(false);
  };

  const handleAutoSelectAllBanks = async () => {
    if (availableBanks.length === 0) return;
    setIsProcessing(true);
    setStatusMessage("Loading all banks...");
    setProgress(0);

    const allBankSamples: Sample[] = [...allSamples];
    const pathsToLoad = availableBanks.filter(b => !selectedBankPaths.includes(b.path));
    const total = pathsToLoad.length;

    for (let i = 0; i < total; i++) {
      const bankMeta = pathsToLoad[i];
      setStatusMessage(`Loading ${bankMeta.name} (${i + 1}/${total})...`);
      setProgress(Math.round(((i + 1) / total) * 80));
      try {
        const response = await fetch(bankMeta.download_url);
        const data = await response.json();
        const { samples } = parseSoundbankJSON(data, bankMeta.path, bankMeta.name, mode);
        if (samples.length > 0) {
          allBankSamples.push(...samples);
          // Also load the bank into the app state
          setSelectedBankPaths(prev => prev.includes(bankMeta.path) ? prev : [...prev, bankMeta.path]);
          setLoadedBanks(prev => prev.some(b => b.id === bankMeta.path) ? prev : [...prev, {
            id: bankMeta.path,
            name: bankMeta.name,
            mode,
            samples,
          }]);
          if (!activeBankPath) setActiveBankPath(bankMeta.path);
        }
      } catch (err) {
        addLog(`Failed to load ${bankMeta.name}: ${err}`, 'error');
      }
    }

    setStatusMessage("Physics-informed anchor selection across all banks...");
    setProgress(90);
    const suggested = suggestAnchors(allBankSamples, mode);
    setAnchorRefs(suggested);
    setIsAutoMode(true);
    setIsCorrected(false);
    setProgress(100);
    addLog(`All ${availableBanks.length} banks loaded. Auto-selected ${suggested.length} anchors from full dataset.`, 'success');
    setIsProcessing(false);
  };

  const handleManualAddAnchor = () => {
    if (!activeBankPath) {
      addLog("No active bank selected. Load a bank and set it as anchor source.", "error");
      return;
    }
    const activeBank = loadedBanks.find(b => b.id === activeBankPath);
    if (!activeBank) {
      addLog("Active bank not found in loaded banks.", "error");
      return;
    }

    // Parse input: "55" or "55/127" (midi/velocity)
    const parts = manualMidi.trim().split('/');
    const midi = parseInt(parts[0]);
    const vel = parts.length > 1 ? parseInt(parts[1]) : undefined;
    if (isNaN(midi) || (vel !== undefined && isNaN(vel))) {
      addLog("Invalid format. Use MIDI (e.g. 55) or MIDI/velocity (e.g. 55/127)", "error");
      return;
    }

    // Find matching samples in the active bank
    const bankSamples = activeBank.samples.filter(s => s.midi === midi && (vel === undefined || s.vel === vel));
    if (bankSamples.length === 0) {
      const available = [...new Set(activeBank.samples.map(s => s.midi))].sort((a, b) => a - b);
      addLog(`MIDI ${midi}${vel !== undefined ? `/vel ${vel}` : ''} not found in ${activeBank.name}. Available: ${available.slice(0, 20).join(', ')}${available.length > 20 ? '...' : ''}`, "error");
      return;
    }

    // Add anchors — if vel specified, add one; otherwise add for all velocities
    let addedCount = 0;
    for (const sample of bankSamples) {
      const ref: AnchorRef = { midi: sample.midi, bankId: sample.bankId, vel: sample.vel };
      const exists = anchorRefs.some(r => r.midi === ref.midi && r.bankId === ref.bankId && r.vel === ref.vel);
      if (!exists) {
        setAnchorRefs(prev => [...prev, ref]);
        addedCount++;
      }
    }

    setManualMidi("");
    setIsAutoMode(false);
    setIsCorrected(false);
    const velStr = vel !== undefined ? `/vel ${vel}` : bankSamples.length > 1 ? ` (all ${bankSamples.length} velocities)` : '';
    addLog(`Anchor added: MIDI ${midi}${velStr} from ${activeBank.name}`, "success");
  };

  const handleCorrectDataset = () => {
    if (allSamples.length === 0 || anchorRefs.length < 2) {
      addLog("Need at least 2 anchors for correction", "error");
      return;
    }

    const config: CorrectionConfig = {
      method: correctionMethod,
      threshold,
      zScoreLimit,
      iqrMultiplier,
    };

    const { corrected, removed, kept } = applyCorrection(analysisResults, config);
    setCorrectedResults(corrected);

    const cleaned = cleanDataset(corrected, allSamples);
    setCorrectedSamples(cleaned);
    setIsCorrected(true);

    addLog(`Correction [${correctionMethod}]: ${kept} kept, ${removed} removed (${allSamples.length} total)`, "success");
  };

  const handleDownload = () => {
    if (loadedBanks.length === 0) return;
    if (anchorRefs.length < 2) {
      addLog("Need at least 2 anchors for export", "error");
      return;
    }

    const sourceResults = isCorrected ? correctedResults : analysisResults;

    const { metadata, notes, stats } = generateCompleteBank(
      allSamples,
      sourceResults,
      anchorRefs,
      mode,
      {
        correctionMethod,
        threshold,
        sourceNames: selectedBankPaths.map(p => availableBanks.find(b => b.path === p)?.name || p),
      }
    );

    const exportData = { metadata, notes };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `merged-${mode}-cleaned.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    addLog(
      `Exported: merged-${mode}-cleaned.json — ${stats.totalNotes} notes (${stats.fromOriginal} original, ${stats.interpolated} interpolated) → Downloads folder`,
      'success'
    );
  };

  const handleExportAnchors = () => {
    if (anchorRefs.length === 0) {
      addLog("No anchors to export", "error");
      return;
    }

    // Build anchor data with full sample params
    const anchorData = anchorRefs.map(ref => {
      const sample = allSamples.find(s => s.midi === ref.midi && s.bankId === ref.bankId && (ref.vel === undefined || s.vel === ref.vel));
      const bankName = loadedBanks.find(b => b.id === ref.bankId)?.name || ref.bankId;
      return {
        midi: ref.midi,
        vel: ref.vel,
        bankId: ref.bankId,
        bankName,
        ...(sample?.physical ? { physical: sample.physical } : {}),
        ...(sample?.additive ? { additive: sample.additive } : {}),
      };
    });

    const exportObj = {
      metadata: {
        generated_by: "ICR Soundbank Analyzer v4.0",
        type: "anchor-set",
        mode,
        timestamp: new Date().toISOString(),
        count: anchorRefs.length,
      },
      anchors: anchorData,
    };

    const now = new Date();
    const ts = [
      String(now.getFullYear()).slice(2),
      String(now.getMonth() + 1).padStart(2, '0'),
      String(now.getDate()).padStart(2, '0'),
      String(now.getHours()).padStart(2, '0'),
      String(now.getMinutes()).padStart(2, '0'),
    ].join('');

    const filename = `anchor-export-${ts}.json`;
    const blob = new Blob([JSON.stringify(exportObj, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    addLog(`Anchors exported: ${filename} (${anchorRefs.length} anchors) → saved to your browser's Downloads folder`, 'success');
  };

  const handleImportAnchors = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target?.result as string);
        if (!data.anchors || !Array.isArray(data.anchors)) {
          addLog("Invalid anchor file: missing 'anchors' array", "error");
          return;
        }

        const imported: AnchorRef[] = data.anchors.map((a: any) => ({
          midi: a.midi,
          vel: a.vel,
          bankId: a.bankId,
        }));

        // Merge with existing anchors (avoid duplicates)
        setAnchorRefs(prev => {
          const merged = [...prev];
          for (const ref of imported) {
            const exists = merged.some(r => r.midi === ref.midi && r.bankId === ref.bankId && r.vel === ref.vel);
            if (!exists) merged.push(ref);
          }
          return merged;
        });

        setIsAutoMode(false);
        setIsCorrected(false);
        addLog(`Imported ${imported.length} anchors from ${file.name} (merged with existing)`, 'success');
      } catch (err) {
        addLog(`Failed to parse anchor file: ${err}`, 'error');
      }
    };
    reader.readAsText(file);
    // Reset input so same file can be re-imported
    e.target.value = '';
  };

  // Loading screen
  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-50 text-zinc-900 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <RefreshCw className="w-8 h-8 animate-spin text-blue-600" />
          <p className="text-sm font-mono animate-pulse">{statusMessage}</p>
        </div>
      </div>
    );
  }

  // ======================= SHARED PANELS =======================

  const renderBankSelector = () => (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
          <Database className="w-4 h-4 text-blue-600" />
          {mode === 'physical' ? 'Physical' : 'Additive'} Repository
        </CardTitle>
        <CardDescription className="text-xs text-zinc-500">Select banks to load. Click the radio to set anchor source bank.</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <ScrollArea className="h-[200px] px-4 pb-4">
          <div className="space-y-2">
            {availableBanks.map((bank) => {
              const isLoaded = selectedBankPaths.includes(bank.path);
              const isActive = activeBankPath === bank.path;
              return (
                <div
                  key={bank.path}
                  className={`flex items-center space-x-3 p-2 rounded-md transition-colors ${isActive ? 'bg-emerald-50 border border-emerald-300' : isLoaded ? 'bg-blue-50 border border-blue-200' : 'hover:bg-zinc-100 border border-transparent'}`}
                >
                  <Checkbox
                    id={`bank-${bank.path}`}
                    checked={isLoaded}
                    onCheckedChange={() => handleBankToggle(bank.path)}
                    disabled={isProcessing}
                  />
                  {isLoaded && (
                    <button
                      type="button"
                      className={`w-4 h-4 rounded-full border-2 flex-shrink-0 transition-colors ${isActive ? 'border-emerald-500 bg-emerald-500' : 'border-zinc-300 bg-white hover:border-emerald-400'}`}
                      onClick={() => setActiveBankPath(bank.path)}
                      title="Set as anchor source bank"
                    >
                      {isActive && <span className="block w-1.5 h-1.5 bg-white rounded-full mx-auto" />}
                    </button>
                  )}
                  <label
                    htmlFor={`bank-${bank.path}`}
                    className="text-xs font-medium cursor-pointer flex-1 text-zinc-700"
                  >
                    {bank.name}
                  </label>
                  {isActive && (
                    <Badge variant="outline" className="text-[9px] border-emerald-400 text-emerald-700 bg-emerald-50">ANCHOR SRC</Badge>
                  )}
                  {isLoaded && !isActive && (
                    <Badge variant="outline" className="text-[9px] border-blue-300 text-blue-600 bg-blue-50">LOADED</Badge>
                  )}
                </div>
              );
            })}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );

  const renderAnalysisParams = () => (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
          <Settings2 className="w-4 h-4 text-blue-600" />
          Analysis Parameters
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Detection Method */}
        <div className="space-y-2">
          <label className="text-[11px] uppercase tracking-wider text-zinc-500 font-bold">Detection Method</label>
          <div className="flex gap-1">
            {([
              { value: 'per-parameter' as DetectionMethod, label: 'Per-Parameter', desc: 'Each param independently' },
              { value: 'physics-constrained' as DetectionMethod, label: 'Physics-Constrained', desc: 'Cross-param consistency' },
            ]).map(opt => (
              <button
                key={opt.value}
                type="button"
                className={`flex-1 px-2 py-1.5 rounded text-[10px] font-medium transition-colors border ${
                  detectionMethod === opt.value
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-zinc-600 border-zinc-200 hover:bg-zinc-50'
                }`}
                onClick={() => { setDetectionMethod(opt.value); setIsCorrected(false); }}
                disabled={isProcessing}
              >
                <div className="font-bold">{opt.label}</div>
                <div className={`text-[8px] ${detectionMethod === opt.value ? 'text-blue-200' : 'text-zinc-400'}`}>{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Threshold */}
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <label className="text-[11px] uppercase tracking-wider text-zinc-500 font-bold">Deviation Threshold</label>
            <span className="text-xs font-mono text-blue-600 font-bold">{(threshold * 100).toFixed(1)}%</span>
          </div>
          <Slider
            value={[threshold]}
            min={0.01}
            max={0.3}
            step={0.005}
            onValueChange={(val: any) => {
              const v = Array.isArray(val) ? val[0] : val;
              if (typeof v === 'number' && !isNaN(v)) {
                setThreshold(v);
                setIsCorrected(false);
              }
            }}
            disabled={isProcessing}
          />
        </div>

        {/* Anchors */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wider text-zinc-500 font-bold flex items-center gap-1.5">
              <Anchor className="w-3 h-3" /> Anchors ({anchorRefs.length})
            </span>
            <div className="flex gap-1">
              <Button
                variant="secondary"
                size="sm"
                className="h-6 px-2 text-[10px] bg-blue-600 hover:bg-blue-700 text-white font-bold shadow-sm"
                onClick={handleAutoSelect}
                disabled={isProcessing || allSamples.length === 0}
                title="Auto-select anchors from loaded banks"
              >
                <RefreshCw className="w-2.5 h-2.5 mr-1" /> AUTO
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-6 px-2 text-[10px] bg-emerald-600 hover:bg-emerald-700 text-white font-bold shadow-sm"
                onClick={handleAutoSelectAllBanks}
                disabled={isProcessing || availableBanks.length === 0}
                title="Load ALL banks and auto-select anchors from full dataset"
              >
                <Database className="w-2.5 h-2.5 mr-1" /> ALL
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-6 px-1.5 text-[10px]"
                onClick={handleExportAnchors}
                disabled={anchorRefs.length === 0}
                title="Export anchors to JSON"
              >
                <Download className="w-3 h-3" />
              </Button>
              <label className="inline-flex items-center" title="Import anchors from JSON">
                <input type="file" accept=".json" className="hidden" onChange={handleImportAnchors} />
                <span className={`inline-flex items-center justify-center h-6 px-1.5 text-[10px] border border-zinc-200 rounded-md cursor-pointer hover:bg-zinc-50 ${isProcessing ? 'opacity-50 pointer-events-none' : ''}`}>
                  <Upload className="w-3 h-3" />
                </span>
              </label>
            </div>
          </div>

          <div className="flex gap-2">
            <input
              type="text"
              placeholder="MIDI or MIDI/vel (e.g. 55 or 55/127)"
              className="flex-1 bg-zinc-50 border border-zinc-200 rounded px-2 py-1 text-[10px] focus:outline-none focus:ring-1 focus:ring-blue-500"
              value={manualMidi}
              onChange={(e) => setManualMidi(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleManualAddAnchor()}
            />
            <Button variant="outline" size="sm" className="h-7 text-[9px] px-2" onClick={handleManualAddAnchor} disabled={isProcessing || !manualMidi}>
              ADD
            </Button>
          </div>

          <ScrollArea className="h-[100px] border border-zinc-100 rounded p-2 bg-zinc-50/50">
            <div className="flex flex-wrap gap-1.5">
              {anchorRefs.length > 0 ? anchorRefs.map(ref => {
                const bankName = loadedBanks.find(b => b.id === ref.bankId)?.name || ref.bankId;
                const velLabel = ref.vel !== undefined ? `/v${ref.vel}` : '';
                return (
                  <Badge
                    key={`${ref.bankId}-${ref.midi}-${ref.vel ?? 'all'}`}
                    variant="secondary"
                    className="bg-blue-100 text-blue-700 border-blue-200 hover:bg-blue-200 cursor-pointer text-[9px]"
                    onClick={() => !isProcessing && toggleAnchor(ref.midi, ref.bankId)}
                    title={`MIDI ${ref.midi}${ref.vel !== undefined ? ` vel ${ref.vel}` : ''} from ${bankName}`}
                  >
                    {ref.midi}{velLabel} <span className="opacity-40">({bankName})</span> <span className="ml-1 opacity-50">x</span>
                  </Badge>
                );
              }) : (
                <p className="text-[10px] text-zinc-400 italic p-2">No anchors. Use AUTO-SELECT or click on chart.</p>
              )}
            </div>
          </ScrollArea>
        </div>
      </CardContent>
    </Card>
  );

  const renderCorrectionPanel = () => (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
          <Filter className="w-4 h-4 text-blue-600" />
          Dataset Correction
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Method selection */}
        <div className="space-y-2">
          <label className="text-[11px] uppercase tracking-wider text-zinc-500 font-bold">Method</label>
          <div className="grid grid-cols-2 gap-1.5">
            {([
              ['threshold', 'Threshold'],
              ['zscore', 'Z-Score'],
              ['iqr', 'IQR'],
              ['interpolate', 'Interpolate'],
            ] as [CorrectionMethod, string][]).map(([method, label]) => (
              <button
                key={method}
                className={`px-2 py-1.5 text-[10px] rounded border font-medium transition-colors ${
                  correctionMethod === method
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-zinc-600 border-zinc-200 hover:bg-zinc-50'
                }`}
                onClick={() => { setCorrectionMethod(method); setIsCorrected(false); }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Method-specific params */}
        {correctionMethod === 'zscore' && (
          <div className="space-y-2">
            <div className="flex justify-between">
              <label className="text-[10px] text-zinc-500 font-bold">Z-Score Limit</label>
              <span className="text-[10px] font-mono text-blue-600">{zScoreLimit.toFixed(1)}σ</span>
            </div>
            <Slider
              value={[zScoreLimit]}
              min={0.5} max={4} step={0.1}
              onValueChange={(val: any) => {
                const v = Array.isArray(val) ? val[0] : val;
                if (typeof v === 'number' && !isNaN(v)) { setZScoreLimit(v); setIsCorrected(false); }
              }}
            />
          </div>
        )}

        {correctionMethod === 'iqr' && (
          <div className="space-y-2">
            <div className="flex justify-between">
              <label className="text-[10px] text-zinc-500 font-bold">IQR Multiplier</label>
              <span className="text-[10px] font-mono text-blue-600">{iqrMultiplier.toFixed(1)}x</span>
            </div>
            <Slider
              value={[iqrMultiplier]}
              min={0.5} max={3} step={0.1}
              onValueChange={(val: any) => {
                const v = Array.isArray(val) ? val[0] : val;
                if (typeof v === 'number' && !isNaN(v)) { setIqrMultiplier(v); setIsCorrected(false); }
              }}
            />
          </div>
        )}

        <Button
          className="w-full bg-blue-600 hover:bg-blue-700 text-white text-xs h-8 font-bold"
          disabled={isProcessing || allSamples.length === 0 || anchorRefs.length < 2}
          onClick={handleCorrectDataset}
        >
          <Filter className="w-3.5 h-3.5 mr-1.5" />
          Apply Correction ({correctionMethod})
        </Button>

        {isCorrected && (
          <div className="p-2 rounded bg-green-50 border border-green-200 text-[10px] text-green-800">
            <strong>Corrected:</strong> {correctedResults.filter(r => r.isGood).length} kept / {correctedResults.filter(r => !r.isGood).length} removed
          </div>
        )}
      </CardContent>
    </Card>
  );

  const renderProjectionControls = () => (
    <Card className="bg-white border-zinc-200 shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
          <Layers className="w-4 h-4 text-blue-600" />
          Projection
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-1.5">
          {([['pca', 'PCA'], ['spectral', 'Spectral'], ['parameter-pair', 'Params']] as [ProjectionMethod, string][]).map(([m, label]) => (
            <button
              key={m}
              className={`px-2 py-1 text-[10px] rounded border font-medium transition-colors flex-1 ${
                projectionMethod === m
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-zinc-600 border-zinc-200 hover:bg-zinc-50'
              }`}
              onClick={() => setProjectionMethod(m)}
            >
              {label}
            </button>
          ))}
        </div>

        {projectionMethod === 'parameter-pair' && (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[9px] text-zinc-500 font-bold uppercase">X Axis</label>
              <select
                className="w-full mt-1 text-[10px] bg-zinc-50 border border-zinc-200 rounded px-1 py-1"
                value={projParamX}
                onChange={e => setProjParamX(e.target.value)}
              >
                {projParams.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[9px] text-zinc-500 font-bold uppercase">Y Axis</label>
              <select
                className="w-full mt-1 text-[10px] bg-zinc-50 border border-zinc-200 rounded px-1 py-1"
                value={projParamY}
                onChange={e => setProjParamY(e.target.value)}
              >
                {projParams.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );

  const renderMainContent = () => (
    <div className="lg:col-span-8 space-y-6">
      {/* Deviation Overview */}
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader className="pb-2 border-b border-zinc-50">
          <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-blue-600" />
            Deviation Overview
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <DatasetOverview
            results={analysisResults}
            onSelectMidi={(midi, bankId) => { setSelectedMidi(midi); setSelectedBankId(bankId); }}
            selectedMidi={selectedMidi}
            threshold={threshold}
          />
        </CardContent>
      </Card>

      {/* Multi-Dimensional Projection: Before vs After */}
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader className="pb-2 border-b border-zinc-50">
          <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
            <Layers className="w-4 h-4 text-blue-600" />
            Cluster Projection
            <Badge variant="outline" className="text-[9px] ml-2">{projectionMethod.toUpperCase()}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <div className={`grid gap-6 ${isCorrected ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'}`} style={{ height: 350 }}>
            <ClusterProjection
              title={isCorrected ? "Before Correction" : "Current Dataset"}
              data={projectedBefore}
              xLabel={projectionMethod === 'parameter-pair' ? projParamX : projectionMethod === 'spectral' ? 'Spectral Centroid' : 'PC1'}
              yLabel={projectionMethod === 'parameter-pair' ? projParamY : projectionMethod === 'spectral' ? 'Spectral Spread' : 'PC2'}
              onClickPoint={(p) => { setSelectedMidi(p.midi); setSelectedBankId(p.bankId); }}
            />
            {isCorrected && (
              <ClusterProjection
                title="After Correction"
                data={projectedAfter}
                xLabel={projectionMethod === 'parameter-pair' ? projParamX : projectionMethod === 'spectral' ? 'Spectral Centroid' : 'PC1'}
                yLabel={projectionMethod === 'parameter-pair' ? projParamY : projectionMethod === 'spectral' ? 'Spectral Spread' : 'PC2'}
                onClickPoint={(p) => { setSelectedMidi(p.midi); setSelectedBankId(p.bankId); }}
              />
            )}
          </div>
        </CardContent>
      </Card>

      {/* Tension Graph */}
      {allSamples.length > 0 && (
        <Card className="bg-white border-zinc-200 shadow-sm">
          <CardHeader className="pb-2 border-b border-zinc-50">
            <CardTitle className="text-sm font-bold text-zinc-800 flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-blue-600" />
              Tension Graph
              <span className="text-[9px] font-normal text-zinc-400 ml-2">Force-directed k-NN similarity</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-4 flex justify-center">
            <TensionGraph
              samples={allSamples}
              mode={mode}
              results={isCorrected ? correctedResults : analysisResults}
              anchorRefs={anchorRefs}
              width={700}
              height={350}
              onClickNode={(midi, bankId) => { setSelectedMidi(midi); setSelectedBankId(bankId); }}
            />
          </CardContent>
        </Card>
      )}

      {/* Detail: Selected sample */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card className="bg-white border-zinc-200 shadow-sm">
          <CardHeader className="pb-2">
            <div className="flex justify-between items-start">
              <div>
                <CardTitle className="text-sm font-semibold text-zinc-800">
                  {mode === 'physical' ? 'Physical Parameters' : 'Spectral Analysis'}
                </CardTitle>
                <CardDescription className="text-xs text-zinc-500">
                  MIDI {selectedMidi} | {selectedResult?.bankName || "No bank"}
                </CardDescription>
              </div>
              <Badge
                variant={selectedResult?.isGood ? "outline" : "destructive"}
                className={`text-[10px] font-bold ${selectedResult?.isGood ? 'border-green-200 text-green-700 bg-green-50' : ''}`}
              >
                {selectedResult?.isAnchor ? "ANCHOR" : selectedResult?.isGood ? "STABLE" : "ANOMALY"}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            {selectedSample ? (
              mode === 'physical' && selectedSample.physical ? (
                <PhysicalVisualizer
                  params={selectedSample.physical}
                  predictedParams={selectedResult?.predictedPhysical}
                  title="Actual vs Model"
                />
              ) : selectedSample.harmonics ? (
                <HarmonicVisualizer
                  harmonics={selectedSample.harmonics}
                  predictedHarmonics={selectedResult?.predictedHarmonics}
                  title="Actual vs Model"
                />
              ) : (
                <div className="h-[300px] flex items-center justify-center text-zinc-400 text-xs italic">No data</div>
              )
            ) : (
              <div className="h-[300px] flex items-center justify-center text-zinc-400 text-xs italic">
                Select a sample from the charts
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="bg-white border-zinc-200 shadow-sm">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold text-zinc-800">Synthesis Core Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="p-3 rounded-lg bg-zinc-50 border border-zinc-100 space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-[10px] uppercase text-zinc-500 font-bold">Active banks</span>
                <span className="text-xs font-mono font-bold text-zinc-700">{selectedBankPaths.length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[10px] uppercase text-zinc-500 font-bold">Total samples</span>
                <span className="text-xs font-mono font-bold text-zinc-700">{allSamples.length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[10px] uppercase text-zinc-500 font-bold">Unique MIDIs</span>
                <span className="text-xs font-mono font-bold text-zinc-700">{new Set(allSamples.map(s => s.midi)).size}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[10px] uppercase text-zinc-500 font-bold">Anchor status</span>
                <Button
                  variant="ghost" size="sm"
                  className={`h-6 px-2 text-[10px] font-bold ${selectedResult?.isAnchor ? 'text-green-600 bg-green-100' : 'text-zinc-400 hover:bg-zinc-100'}`}
                  onClick={() => selectedSample && toggleAnchor(selectedMidi, selectedBankId)}
                  disabled={isProcessing || !selectedSample}
                >
                  <Anchor className="w-3 h-3 mr-1" />
                  {selectedResult?.isAnchor ? "Active anchor" : "Set as anchor"}
                </Button>
              </div>
            </div>

            {selectedResult && (
              <div className="p-3 rounded-lg bg-blue-50 border border-blue-100 space-y-1 text-[10px]">
                <div className="flex justify-between">
                  <span className="text-blue-600 font-bold">Deviation</span>
                  <span className="font-mono">{selectedResult.deviation.toFixed(6)}</span>
                </div>
                {selectedResult.zScore !== undefined && (
                  <div className="flex justify-between">
                    <span className="text-blue-600 font-bold">Z-Score</span>
                    <span className="font-mono">{selectedResult.zScore.toFixed(3)}</span>
                  </div>
                )}
                {selectedResult.isolationScore !== undefined && (
                  <div className="flex justify-between">
                    <span className="text-blue-600 font-bold">Isolation</span>
                    <span className="font-mono">{selectedResult.isolationScore.toFixed(4)}</span>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );

  // ======================= MAIN RENDER =======================

  return (
    <TooltipProvider>
      <div className="min-h-screen bg-zinc-50 text-zinc-900 p-4 md:p-8 font-sans selection:bg-blue-600/20">
        {/* Processing overlay */}
        <AnimatePresence>
          {isProcessing && (
            <motion.div
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              className="fixed top-0 left-0 right-0 z-50 p-4 flex justify-center"
            >
              <Card className="w-full max-w-md bg-white/90 border-blue-600/50 backdrop-blur-md shadow-2xl shadow-blue-600/10">
                <CardContent className="pt-6 space-y-3">
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-blue-600 flex items-center gap-2 text-[10px] uppercase tracking-wider font-bold">
                      <RefreshCw className="w-3 h-3 animate-spin" />
                      {statusMessage}
                    </span>
                    <span className="font-bold">{progress}%</span>
                  </div>
                  <Progress value={progress} className="h-2 bg-zinc-200" />
                </CardContent>
              </Card>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Header */}
        <header className="max-w-7xl mx-auto mb-8 flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="outline" className="border-blue-600/50 text-blue-600 bg-blue-600/5 px-2 py-0.5 text-[10px] uppercase tracking-widest font-bold">
                ICR Engine v4.0
              </Badge>
              {isAutoMode && (
                <Badge variant="secondary" className="bg-green-600/10 text-green-600 border-green-600/20 text-[10px] uppercase tracking-widest font-bold">
                  Auto-mode
                </Badge>
              )}
              {isCorrected && (
                <Badge variant="secondary" className="bg-amber-600/10 text-amber-600 border-amber-600/20 text-[10px] uppercase tracking-widest font-bold">
                  Corrected ({correctionMethod})
                </Badge>
              )}
            </div>
            <h1 className="text-4xl font-bold tracking-tight text-zinc-900 mb-2">Soundbank Analyzer</h1>
            <p className="text-zinc-500 max-w-2xl text-sm leading-relaxed">
              Analyze, validate and merge soundbank datasets. Multi-dimensional cluster projection, anomaly detection, and dataset correction.
            </p>
          </div>
          <div className="flex gap-3">
            <Button
              variant="outline"
              className="border-zinc-200 bg-white hover:bg-zinc-50 text-xs h-9 shadow-sm"
              disabled={isProcessing || loadedBanks.length === 0 || anchorRefs.length < 2}
              onClick={handleDownload}
            >
              <Download className="w-4 h-4 mr-2" />
              Export Merged Bank
            </Button>
          </div>
        </header>

        {/* Main content with tabs */}
        <main className="max-w-7xl mx-auto space-y-6">
          <Tabs value={activeTab} onValueChange={(v) => {
            const tab = v as AppTab;
            setActiveTab(tab);
            if (tab === 'physical' || tab === 'additive') setMode(tab);
          }} className="w-full">
            <TabsList className="bg-zinc-100 border border-zinc-200 p-1 mb-6">
              <TabsTrigger value="physical" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white text-xs px-6">
                <Cpu className="w-4 h-4 mr-2" /> Physical
              </TabsTrigger>
              <TabsTrigger value="additive" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white text-xs px-6">
                <Waves className="w-4 h-4 mr-2" /> Additive
              </TabsTrigger>
              <TabsTrigger value="docs" className="data-[state=active]:bg-zinc-700 data-[state=active]:text-white text-xs px-6">
                <FileText className="w-4 h-4 mr-2" /> Guide
              </TabsTrigger>
            </TabsList>

            <TabsContent value="docs" className="mt-0">
              <SystemGuide />
            </TabsContent>

            {/* Both physical and additive share the same layout */}
            {(['physical', 'additive'] as const).map(tabMode => (
              <TabsContent key={tabMode} value={tabMode} className="mt-0">
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                  <div className="lg:col-span-4 space-y-6">
                    {renderBankSelector()}
                    {renderAnalysisParams()}
                    {renderCorrectionPanel()}
                    {renderProjectionControls()}
                  </div>
                  {renderMainContent()}
                </div>
              </TabsContent>
            ))}
          </Tabs>
        </main>

        {/* Log */}
        <div className="max-w-7xl mx-auto mt-12 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500 flex items-center gap-2">
              <Activity className="w-4 h-4" /> System Log
            </h2>
            <Button variant="ghost" size="sm" className="text-[10px] text-zinc-400 hover:text-zinc-600" onClick={() => setLogs([])}>
              CLEAR
            </Button>
          </div>
          <Card className="bg-white border-zinc-200 shadow-sm overflow-hidden">
            <ScrollArea className="h-[200px] bg-zinc-50/50">
              <div className="p-4 font-mono text-[11px] space-y-1.5">
                {logs.length > 0 ? logs.map((log, i) => (
                  <div key={i} className="flex gap-3 border-b border-zinc-100 pb-1.5 last:border-0">
                    <span className="text-zinc-400 shrink-0">[{log.timestamp}]</span>
                    <span className={`shrink-0 font-bold uppercase w-16 ${
                      log.level === 'error' ? 'text-red-600' :
                      log.level === 'success' ? 'text-green-600' :
                      log.level === 'debug' ? 'text-blue-600' :
                      'text-zinc-500'
                    }`}>
                      {log.level}
                    </span>
                    <span className="text-zinc-700">{log.message}</span>
                  </div>
                )) : (
                  <div className="text-zinc-400 italic text-center py-8">No activity logged.</div>
                )}
              </div>
            </ScrollArea>
          </Card>
          <p className="text-[10px] text-zinc-400 text-center">ICR Soundbank Analyzer v4.0</p>
        </div>
      </div>
    </TooltipProvider>
  );
}
