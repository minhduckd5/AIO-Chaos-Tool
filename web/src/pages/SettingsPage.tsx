import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Settings as SettingsIcon,
  Save,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import { api } from '../lib/api';

export const SettingsPage: React.FC = () => {
  const queryClient = useQueryClient();

  const [formSettings, setFormSettings] = useState<Record<string, any>>({});
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [sweepResult, setSweepResult] = useState<string | null>(null);

  const { data: settingsData, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  useEffect(() => {
    if (settingsData?.settings) {
      setFormSettings(settingsData.settings);
    }
  }, [settingsData]);

  const updateMutation = useMutation({
    mutationFn: (updated: Record<string, unknown>) => api.updateSettings(updated),
    onSuccess: () => {
      setSaveSuccess(true);
      setSaveError(null);
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setTimeout(() => setSaveSuccess(false), 4000);
    },
    onError: (err: any) => {
      setSaveError(err.message || 'Failed to save settings');
    },
  });

  const sweepMutation = useMutation({
    mutationFn: () => api.sweepOrphans(3600),
    onSuccess: (res) => {
      setSweepResult(`Successfully swept ${res.removed_count} orphaned resource(s).`);
      queryClient.invalidateQueries({ queryKey: ['audit'] });
      setTimeout(() => setSweepResult(null), 5000);
    },
    onError: (err: any) => {
      alert(`Orphan sweep failed: ${err.message}`);
    },
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    updateMutation.mutate(formSettings);
  };

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto font-mono text-xs">
      {/* Header */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div className="flex items-center gap-2">
            <SettingsIcon className="w-5 h-5 text-zinc-300" />
            <h1 className="text-lg font-bold text-zinc-100 tracking-tight">
              CONFIGURATION & GOVERNANCE SETTINGS
            </h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1 max-w-2xl">
            Manage LLM provider endpoints, telemetry collector parameters, and blast radius constraints.
            Secrets and API keys are masked for zero-leak compliance.
          </p>
        </div>
      </div>

      {saveSuccess && (
        <div className="p-3 bg-emerald-950/80 border border-emerald-700 text-emerald-300 rounded flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>Configuration saved and orchestrator reloaded successfully.</span>
        </div>
      )}

      {saveError && (
        <div className="p-3 bg-rose-950/80 border border-rose-700 text-rose-300 rounded flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-400" />
          <span>{saveError}</span>
        </div>
      )}

      {sweepResult && (
        <div className="p-3 bg-blue-950/80 border border-blue-700 text-blue-300 rounded flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-blue-400" />
          <span>{sweepResult}</span>
        </div>
      )}

      {isLoading ? (
        <div className="p-12 text-center text-zinc-500">Loading settings...</div>
      ) : (
        <form onSubmit={handleSave} className="space-y-6">
          {/* Section 1: General & Identity */}
          <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 space-y-4">
            <h2 className="text-sm font-semibold text-zinc-200 uppercase tracking-wider border-b border-industrial-800 pb-2">
              1. Identity & Execution Mode
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-zinc-400 block mb-1">Default Operator Name (Audit Actor)</label>
                <input
                  type="text"
                  value={formSettings.operator_name || ''}
                  onChange={(e) =>
                    setFormSettings({ ...formSettings, operator_name: e.target.value })
                  }
                  className="w-full bg-industrial-950 border border-industrial-800 px-3 py-1.5 rounded text-zinc-200 focus:outline-none focus:border-cyan-500"
                  placeholder="e.g. lead-sre"
                />
              </div>

              <div>
                <label className="text-zinc-400 block mb-1">Developer Mode</label>
                <label className="flex items-center gap-2 mt-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={Boolean(formSettings.developer_mode)}
                    onChange={(e) =>
                      setFormSettings({ ...formSettings, developer_mode: e.target.checked })
                    }
                    className="w-4 h-4 rounded bg-industrial-950 border-industrial-700 text-cyan-600 focus:ring-0"
                  />
                  <span className="text-zinc-300">Enable advanced tuning & verbose debug logs</span>
                </label>
              </div>
            </div>
          </div>

          {/* Section 2: LLM Advisor Configuration */}
          <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 space-y-4">
            <h2 className="text-sm font-semibold text-zinc-200 uppercase tracking-wider border-b border-industrial-800 pb-2">
              2. AI Advisor & LLM Provider
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-zinc-400 block mb-1">LLM Provider</label>
                <select
                  value={formSettings.llm_provider || 'ollama'}
                  onChange={(e) =>
                    setFormSettings({ ...formSettings, llm_provider: e.target.value })
                  }
                  className="w-full bg-industrial-950 border border-industrial-800 px-3 py-1.5 rounded text-zinc-200 focus:outline-none focus:border-cyan-500"
                >
                  <option value="ollama">Ollama (Local)</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai">OpenAI</option>
                  <option value="claude">Anthropic Claude</option>
                </select>
              </div>

              <div>
                <label className="text-zinc-400 block mb-1">Model Slug / Identifier</label>
                <input
                  type="text"
                  value={formSettings.llm_model || ''}
                  onChange={(e) =>
                    setFormSettings({ ...formSettings, llm_model: e.target.value })
                  }
                  placeholder="e.g. deepseek-r1:8b or gpt-4o"
                  className="w-full bg-industrial-950 border border-industrial-800 px-3 py-1.5 rounded text-zinc-200 focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>
          </div>

          {/* Section 3: Safety & Blast Radius */}
          <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 space-y-4">
            <h2 className="text-sm font-semibold text-zinc-200 uppercase tracking-wider border-b border-industrial-800 pb-2">
              3. Safety Policy & Blast Radius Limits
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-zinc-400 block mb-1">Max Services Per Suite</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={formSettings.safety?.max_services_per_suite || 3}
                  onChange={(e) =>
                    setFormSettings({
                      ...formSettings,
                      safety: {
                        ...formSettings.safety,
                        max_services_per_suite: Number(e.target.value),
                      },
                    })
                  }
                  className="w-full bg-industrial-950 border border-industrial-800 px-3 py-1.5 rounded text-zinc-200 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div>
                <label className="text-zinc-400 block mb-1">Max Affected Nodes</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={formSettings.safety?.max_affected_nodes || 3}
                  onChange={(e) =>
                    setFormSettings({
                      ...formSettings,
                      safety: {
                        ...formSettings.safety,
                        max_affected_nodes: Number(e.target.value),
                      },
                    })
                  }
                  className="w-full bg-industrial-950 border border-industrial-800 px-3 py-1.5 rounded text-zinc-200 focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>
          </div>

          {/* Section 4: Maintenance & Orphan Sweep */}
          <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 space-y-4">
            <h2 className="text-sm font-semibold text-zinc-200 uppercase tracking-wider border-b border-industrial-800 pb-2 flex items-center justify-between">
              <span>4. Maintenance & Resource Garbage Collection</span>
              <span className="text-[10px] text-zinc-500 font-normal">Phase 2 Store</span>
            </h2>

            <div className="flex items-center justify-between p-3.5 bg-industrial-950 border border-industrial-800 rounded">
              <div>
                <div className="font-semibold text-zinc-200">Sweep Orphaned Chaos Resources</div>
                <div className="text-[11px] text-zinc-500 mt-0.5">
                  Removes expired tracked resources from <code className="text-zinc-400">.chaosgen/orphan_resources.json</code> and logs an audit event.
                </div>
              </div>

              <button
                type="button"
                onClick={() => sweepMutation.mutate()}
                disabled={sweepMutation.isPending}
                className="px-3.5 py-1.5 bg-industrial-850 hover:bg-industrial-800 text-zinc-300 border border-industrial-700 rounded transition flex items-center gap-1.5"
              >
                <Trash2 className="w-3.5 h-3.5 text-zinc-400" />
                <span>Trigger Sweep</span>
              </button>
            </div>
          </div>

          {/* Save Action */}
          <div className="flex justify-end items-center gap-3 pt-2">
            <button
              type="submit"
              disabled={updateMutation.isPending}
              className="px-5 py-2.5 bg-cyan-600 hover:bg-cyan-500 text-white font-bold rounded transition flex items-center gap-2 shadow-lg"
            >
              {updateMutation.isPending ? (
                <RotateCcw className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              <span>SAVE CONFIGURATION</span>
            </button>
          </div>
        </form>
      )}
    </div>
  );
};
