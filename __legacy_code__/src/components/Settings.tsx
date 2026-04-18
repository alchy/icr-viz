import React from 'react';
import { Globe, HardDrive, FolderOpen, Save, Upload, RotateCcw } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { AppConfig, DEFAULT_APP_CONFIG, LogEntry } from '../types';

interface SettingsProps {
  config: AppConfig;
  onConfigChange: (config: AppConfig) => void;
  addLog: (message: string, level: LogEntry['level']) => void;
}

export const Settings: React.FC<SettingsProps> = ({ config, onConfigChange, addLog }) => {

  const updatePhysical = (field: string, value: string) => {
    onConfigChange({
      ...config,
      physicalSource: { ...config.physicalSource, [field]: value },
    });
  };

  const updateAdditive = (field: string, value: string) => {
    onConfigChange({
      ...config,
      additiveSource: { ...config.additiveSource, [field]: value },
    });
  };

  const handleSaveConfig = async () => {
    try {
      const res = await fetch('/api/save-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (data.success) {
        addLog('Configuration saved to icr-config.json', 'success');
      } else {
        addLog(`Failed to save config: ${data.error}`, 'error');
      }
    } catch (err) {
      addLog(`Failed to save config: ${err}`, 'error');
    }
  };

  const handleLoadConfig = async () => {
    try {
      const res = await fetch('/api/load-config');
      const data = await res.json();
      if (data && data.version) {
        onConfigChange(data as AppConfig);
        addLog('Configuration loaded from icr-config.json', 'success');
      } else {
        addLog('No saved configuration found (icr-config.json)', 'info');
      }
    } catch (err) {
      addLog(`Failed to load config: ${err}`, 'error');
    }
  };

  const handleImportConfig = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target?.result as string);
        if (!data.version || !data.physicalSource || !data.additiveSource) {
          addLog('Invalid config file: missing required fields', 'error');
          return;
        }
        onConfigChange(data as AppConfig);
        addLog(`Configuration imported from ${file.name}`, 'success');
      } catch (err) {
        addLog(`Failed to parse config file: ${err}`, 'error');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleExportConfig = () => {
    const json = JSON.stringify(config, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'icr-config.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    addLog('Configuration exported as icr-config.json', 'success');
  };

  const handleReset = () => {
    onConfigChange({ ...DEFAULT_APP_CONFIG });
    addLog('Configuration reset to defaults', 'info');
  };

  const inputClass = "w-full bg-zinc-50 border border-zinc-200 rounded px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500 text-zinc-700";
  const labelClass = "text-[11px] uppercase tracking-wider text-zinc-500 font-bold";

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Physical Bank Sources */}
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
            <HardDrive className="w-4 h-4 text-blue-600" />
            Physical Bank Sources
          </CardTitle>
          <CardDescription className="text-xs text-zinc-500">
            GitHub API URL and optional local filesystem path for physical soundbanks.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <label className={labelClass}>
              <Globe className="w-3 h-3 inline mr-1" />GitHub API URL
            </label>
            <input
              type="text"
              className={inputClass}
              value={config.physicalSource.githubUrl}
              onChange={e => updatePhysical('githubUrl', e.target.value)}
              placeholder="https://api.github.com/repos/..."
            />
          </div>
          <div className="space-y-1">
            <label className={labelClass}>
              <FolderOpen className="w-3 h-3 inline mr-1" />Filesystem Path (secondary)
            </label>
            <input
              type="text"
              className={inputClass}
              value={config.physicalSource.filesystemPath}
              onChange={e => updatePhysical('filesystemPath', e.target.value)}
              placeholder="e.g. C:\soundbanks\physical or ./data/physical"
            />
            {config.physicalSource.filesystemPath && (
              <p className="text-[9px] text-zinc-400">Local banks will be merged with GitHub banks in the selector.</p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Additive Bank Sources */}
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
            <HardDrive className="w-4 h-4 text-indigo-600" />
            Additive Bank Sources
          </CardTitle>
          <CardDescription className="text-xs text-zinc-500">
            GitHub API URL and optional local filesystem path for additive soundbanks.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <label className={labelClass}>
              <Globe className="w-3 h-3 inline mr-1" />GitHub API URL
            </label>
            <input
              type="text"
              className={inputClass}
              value={config.additiveSource.githubUrl}
              onChange={e => updateAdditive('githubUrl', e.target.value)}
              placeholder="https://api.github.com/repos/..."
            />
          </div>
          <div className="space-y-1">
            <label className={labelClass}>
              <FolderOpen className="w-3 h-3 inline mr-1" />Filesystem Path (secondary)
            </label>
            <input
              type="text"
              className={inputClass}
              value={config.additiveSource.filesystemPath}
              onChange={e => updateAdditive('filesystemPath', e.target.value)}
              placeholder="e.g. C:\soundbanks\additive or ./data/additive"
            />
          </div>
        </CardContent>
      </Card>

      {/* Output Directory */}
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
            <FolderOpen className="w-4 h-4 text-emerald-600" />
            Export Output
          </CardTitle>
          <CardDescription className="text-xs text-zinc-500">
            Base directory for exported banks. Subdirectories <code className="text-[10px] bg-zinc-100 px-1 rounded">physical/</code> and <code className="text-[10px] bg-zinc-100 px-1 rounded">additive/</code> are created automatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <label className={labelClass}>Base Directory</label>
            <input
              type="text"
              className={inputClass}
              value={config.outputBaseDir}
              onChange={e => onConfigChange({ ...config, outputBaseDir: e.target.value })}
              placeholder="./exports"
            />
          </div>
          <div className="p-2 rounded bg-zinc-50 border border-zinc-100 text-[10px] text-zinc-500 space-y-1">
            <div>Physical exports: <code className="bg-zinc-200/50 px-1 rounded">{config.outputBaseDir}/physical/merged-physical-cleaned-*.json</code></div>
            <div>Additive exports: <code className="bg-zinc-200/50 px-1 rounded">{config.outputBaseDir}/additive/merged-additive-cleaned-*.json</code></div>
            <div>Directories are created automatically if they don't exist.</div>
          </div>
        </CardContent>
      </Card>

      {/* Configuration Management */}
      <Card className="bg-white border-zinc-200 shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2 text-zinc-800">
            <Save className="w-4 h-4 text-amber-600" />
            Configuration
          </CardTitle>
          <CardDescription className="text-xs text-zinc-500">
            Save, load, import or export configuration. Saved to <code className="text-[10px] bg-zinc-100 px-1 rounded">icr-config.json</code> in the project root.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs bg-blue-600 hover:bg-blue-700 text-white border-blue-600"
              onClick={handleSaveConfig}
            >
              <Save className="w-3.5 h-3.5 mr-1.5" /> Save
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={handleLoadConfig}
            >
              <Upload className="w-3.5 h-3.5 mr-1.5" /> Load
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={handleExportConfig}
            >
              <FolderOpen className="w-3.5 h-3.5 mr-1.5" /> Export JSON
            </Button>
            <label className="inline-flex items-center">
              <input type="file" accept=".json" className="hidden" onChange={handleImportConfig} />
              <span className="inline-flex items-center h-8 px-3 text-xs border border-zinc-200 rounded-md cursor-pointer hover:bg-zinc-50 gap-1.5">
                <Upload className="w-3.5 h-3.5" /> Import JSON
              </span>
            </label>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs text-red-600 hover:bg-red-50 border-red-200"
              onClick={handleReset}
            >
              <RotateCcw className="w-3.5 h-3.5 mr-1.5" /> Reset Defaults
            </Button>
          </div>
          <div className="mt-4 p-3 rounded bg-zinc-50 border border-zinc-100">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-bold">Current Config</span>
              <Badge variant="outline" className="text-[9px]">v{config.version}</Badge>
            </div>
            <pre className="text-[9px] font-mono text-zinc-600 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(config, null, 2)}
            </pre>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};
