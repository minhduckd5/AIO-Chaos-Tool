import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Sparkles,
  ShieldAlert,
  Trash2,
  Play,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  Layers,
  Cpu,
} from 'lucide-react';
import { api } from '../lib/api';
import { PendingExperiment } from '../types';

export const TelemetryAdvisorPage: React.FC = () => {
  const queryClient = useQueryClient();

  // HITL Queue State
  const [selectedExperiment, setSelectedExperiment] = useState<{
    index: number;
    exp: PendingExperiment;
  } | null>(null);
  const [armConfirmed, setArmConfirmed] = useState(false);
  const [armError, setArmError] = useState<string | null>(null);

  // Analysis Configuration
  const [lookbackHours, setLookbackHours] = useState(24);
  const [llmProvider, setLlmProvider] = useState('ollama');
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [replaceConfirmNeeded, setReplaceConfirmNeeded] = useState(false);

  // Pending queue query
  const { data: pendingData, isLoading: pendingLoading } = useQuery({
    queryKey: ['pending'],
    queryFn: api.getPending,
    refetchInterval: 3000,
  });

  const experiments = pendingData?.pending || [];

  // Approve mutation
  const approveMutation = useMutation({
    mutationFn: (index: number) => api.approvePending(index),
    onSuccess: (res) => {
      setSelectedExperiment(null);
      setArmConfirmed(false);
      setArmError(null);
      queryClient.invalidateQueries({ queryKey: ['pending'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['history'] });
      if (!res.ran) {
        alert(`Approve failed: ${res.reason || 'Could not execute'}`);
      }
    },
    onError: (err: any) => {
      setArmError(err.message || 'Approval failed');
    },
  });

  // Reject single mutation
  const rejectMutation = useMutation({
    mutationFn: (index: number) => api.rejectPending(index),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });

  // Reject all mutation
  const rejectAllMutation = useMutation({
    mutationFn: () => api.rejectAllPending(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });

  // Analyze mutation
  const analyzeMutation = useMutation({
    mutationFn: (force: boolean) =>
      api.analyzeTelemetry({
        lookback_hours: lookbackHours,
        force,
        llm_provider: llmProvider,
      }),
    onSuccess: () => {
      setAnalysisError(null);
      setReplaceConfirmNeeded(false);
      queryClient.invalidateQueries({ queryKey: ['pending'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
    onError: (err: any) => {
      if (err.status === 409) {
        setReplaceConfirmNeeded(true);
      } else {
        setAnalysisError(err.message || 'Telemetry analysis failed');
      }
    },
  });

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Top Banner: Telemetry & AI Advisor */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-cyan-400" />
            <h1 className="text-lg font-bold text-zinc-100 tracking-tight font-mono">
              TELEMETRY ANOMALY ADVISOR & HITL GATE
            </h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1 max-w-2xl font-mono">
            Analyzes metric deviations across Prometheus & Loki to synthesise targeted chaos experiments.
            No scenario injects without explicit Human-In-The-Loop approval.
          </p>
        </div>

        {/* Analysis Trigger Controls */}
        <div className="flex flex-wrap items-center gap-3 bg-industrial-950 p-2.5 rounded border border-industrial-800 font-mono text-xs">
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-400">Lookback:</span>
            <input
              type="number"
              min={1}
              max={168}
              value={lookbackHours}
              onChange={(e) => setLookbackHours(Number(e.target.value))}
              className="w-16 bg-industrial-900 border border-industrial-700 px-2 py-1 rounded text-zinc-100 text-center focus:border-cyan-500 focus:outline-none"
            />
            <span className="text-zinc-500">hrs</span>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-zinc-400">LLM:</span>
            <select
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value)}
              className="bg-industrial-900 border border-industrial-700 px-2 py-1 rounded text-zinc-200 focus:border-cyan-500 focus:outline-none"
            >
              <option value="ollama">Ollama (Local)</option>
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI</option>
              <option value="claude">Claude</option>
            </select>
          </div>

          <button
            onClick={() => analyzeMutation.mutate(false)}
            disabled={analyzeMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white font-medium rounded transition"
          >
            {analyzeMutation.isPending ? (
              <RotateCcw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Sparkles className="w-3.5 h-3.5" />
            )}
            <span>Analyze Telemetry</span>
          </button>
        </div>
      </div>

      {/* 409 Conflict Banner: Unapproved Queue Replacement Prompt */}
      {replaceConfirmNeeded && (
        <div className="bg-amber-950/80 border border-amber-600 rounded-lg p-4 flex items-center justify-between font-mono text-xs text-amber-200">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
            <div>
              <div className="font-bold uppercase tracking-wider">Unapproved Scenarios Pending in Queue</div>
              <div className="text-amber-300/90 text-[11px] mt-0.5">
                The approval queue contains {experiments.length} unapproved scenario(s). Running new analysis will overwrite them.
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setReplaceConfirmNeeded(false)}
              className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded"
            >
              Cancel
            </button>
            <button
              onClick={() => analyzeMutation.mutate(true)}
              className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-industrial-950 font-bold rounded"
            >
              Force Replace Queue (?force=true)
            </button>
          </div>
        </div>
      )}

      {analysisError && (
        <div className="bg-rose-950/70 border border-rose-700 rounded p-3 text-xs text-rose-300 font-mono flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-400" />
          <span>{analysisError}</span>
        </div>
      )}

      {/* HITL Table Section */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg overflow-hidden">
        {/* Table Header Controls */}
        <div className="p-4 border-b border-industrial-800 flex justify-between items-center bg-industrial-950">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-amber-400" />
            <h2 className="text-sm font-semibold font-mono text-zinc-200 uppercase tracking-wider">
              Pending Human-In-The-Loop Approvals
            </h2>
            <span className="px-2 py-0.5 rounded-full text-xs font-mono font-bold bg-amber-950 text-amber-300 border border-amber-800">
              {experiments.length}
            </span>
          </div>

          {experiments.length > 0 && (
            <button
              onClick={() => {
                if (confirm('Reject and clear all pending experiments in queue?')) {
                  rejectAllMutation.mutate();
                }
              }}
              disabled={rejectAllMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1 bg-industrial-800 hover:bg-rose-950 text-zinc-400 hover:text-rose-300 border border-industrial-700 hover:border-rose-800 rounded text-xs font-mono transition"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Reject All</span>
            </button>
          )}
        </div>

        {/* Table Content */}
        {pendingLoading ? (
          <div className="p-12 text-center font-mono text-xs text-zinc-500">
            Loading approval queue...
          </div>
        ) : experiments.length === 0 ? (
          <div className="p-12 text-center font-mono space-y-2">
            <CheckCircle2 className="w-8 h-8 text-zinc-600 mx-auto" />
            <div className="text-xs text-zinc-400">Approval queue is empty. Orchestrator is IDLE.</div>
            <div className="text-[11px] text-zinc-600">
              Trigger anomaly analysis above or stage scenarios from the Scenario Catalog.
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs border-collapse">
              <thead>
                <tr className="border-b border-industrial-800 bg-industrial-950/60 text-zinc-400 text-[11px] uppercase tracking-wider">
                  <th className="py-3 px-4 font-medium">Scenario / Hypothesis</th>
                  <th className="py-3 px-4 font-medium">Target</th>
                  <th className="py-3 px-4 font-medium">Faults</th>
                  <th className="py-3 px-4 font-medium">Origin & Confidence</th>
                  <th className="py-3 px-4 font-medium text-right">Action Gate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-industrial-800">
                {experiments.map((exp, idx) => (
                  <tr
                    key={idx}
                    className="hover:bg-industrial-850/50 transition-colors"
                  >
                    {/* Column 1: Name & Hypothesis */}
                    <td className="py-3.5 px-4 align-top max-w-sm">
                      <div className="font-semibold text-zinc-100 flex items-center gap-1.5">
                        <span>{exp.name}</span>
                        {exp.consumed && (
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                            consumed
                          </span>
                        )}
                      </div>
                      {exp.hypothesis && (
                        <div className="text-[11px] text-zinc-400 mt-1 line-clamp-2">
                          {exp.hypothesis}
                        </div>
                      )}
                    </td>

                    {/* Column 2: Target & Namespace */}
                    <td className="py-3.5 px-4 align-top whitespace-nowrap">
                      <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-industrial-950 border border-industrial-800 text-cyan-300">
                        <Cpu className="w-3 h-3 text-cyan-400" />
                        <span>{exp.target}</span>
                      </div>
                      {exp.namespace && (
                        <div className="text-[10px] text-zinc-500 mt-0.5">
                          ns: <span className="text-zinc-400">{exp.namespace}</span>
                        </div>
                      )}
                    </td>

                    {/* Column 3: Fault Types */}
                    <td className="py-3.5 px-4 align-top">
                      <div className="flex flex-wrap gap-1">
                        {exp.fault_types.map((f, fIdx) => (
                          <span
                            key={fIdx}
                            className="px-2 py-0.5 rounded bg-rose-950/60 text-rose-300 border border-rose-900 text-[10px]"
                          >
                            {f}
                          </span>
                        ))}
                      </div>
                    </td>

                    {/* Column 4: Origin & Confidence */}
                    <td className="py-3.5 px-4 align-top whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        {exp.origin === 'ai_advisor' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-cyan-950/70 border border-cyan-800 text-cyan-300 text-[10px]">
                            <Sparkles className="w-3 h-3" />
                            AI Advisor
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-purple-950/70 border border-purple-800 text-purple-300 text-[10px]">
                            <Layers className="w-3 h-3" />
                            Catalog
                          </span>
                        )}

                        {exp.confidence !== null && exp.confidence !== undefined && (
                          <span className="text-[11px] font-semibold text-emerald-400">
                            {Math.round(exp.confidence * 100)}%
                          </span>
                        )}
                      </div>

                      {exp.sci_score !== null && exp.sci_score !== undefined && (
                        <div className="text-[10px] text-zinc-500 mt-1">
                          SCI: <span className="text-zinc-300">{exp.sci_score.toFixed(2)}</span>
                        </div>
                      )}
                    </td>

                    {/* Column 5: Action Gate */}
                    <td className="py-3.5 px-4 align-top text-right whitespace-nowrap space-x-2">
                      <button
                        onClick={() => {
                          setSelectedExperiment({ index: idx, exp });
                          setArmConfirmed(false);
                          setArmError(null);
                        }}
                        disabled={approveMutation.isPending}
                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-medium rounded text-xs transition inline-flex items-center gap-1 shadow-sm"
                      >
                        <Play className="w-3 h-3 fill-current" />
                        <span>Arm & Approve</span>
                      </button>

                      <button
                        onClick={() => rejectMutation.mutate(idx)}
                        disabled={rejectMutation.isPending}
                        title="Remove single scenario from queue"
                        className="p-1.5 text-zinc-400 hover:text-rose-400 hover:bg-industrial-950 rounded border border-transparent hover:border-industrial-700 transition inline-flex items-center"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Arm Injection Confirmation Modal */}
      {selectedExperiment && (
        <div className="fixed inset-0 bg-industrial-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-industrial-900 border-2 border-amber-500 rounded-lg max-w-lg w-full p-6 shadow-2xl font-mono space-y-4">
            <div className="flex items-center gap-3 text-amber-400">
              <ShieldAlert className="w-8 h-8 shrink-0" />
              <div>
                <h3 className="text-base font-bold uppercase tracking-wider text-zinc-100">
                  ARM INJECTION — THIS IS NOT A DRILL
                </h3>
                <p className="text-xs text-amber-300">
                  Mission-critical human confirmation gate
                </p>
              </div>
            </div>

            <div className="p-3.5 rounded bg-industrial-950 border border-industrial-800 space-y-2 text-xs">
              <div>
                <span className="text-zinc-400">Experiment:</span>{' '}
                <span className="text-zinc-100 font-semibold">{selectedExperiment.exp.name}</span>
              </div>
              <div>
                <span className="text-zinc-400">Target Service:</span>{' '}
                <span className="text-cyan-300">{selectedExperiment.exp.target}</span>{' '}
                <span className="text-zinc-500">
                  ({selectedExperiment.exp.namespace || 'default'})
                </span>
              </div>
              <div>
                <span className="text-zinc-400">Fault Chain:</span>{' '}
                <span className="text-rose-300">
                  {selectedExperiment.exp.fault_types.join(', ')}
                </span>
              </div>
              {selectedExperiment.exp.hypothesis && (
                <div>
                  <span className="text-zinc-400">Resilience Hypothesis:</span>
                  <p className="text-zinc-300 italic text-[11px] mt-0.5">
                    "{selectedExperiment.exp.hypothesis}"
                  </p>
                </div>
              )}
            </div>

            {/* Arming Checkbox */}
            <label className="flex items-start gap-3 p-3 rounded bg-amber-950/40 border border-amber-900 text-xs text-amber-200 cursor-pointer">
              <input
                type="checkbox"
                checked={armConfirmed}
                onChange={(e) => setArmConfirmed(e.target.checked)}
                className="mt-0.5 rounded border-amber-600 text-amber-500 focus:ring-0 focus:ring-offset-0 bg-industrial-900 w-4 h-4"
              />
              <span>
                I verify the blast radius boundaries, target namespace, and confirm that automated rollback triggers are active.
              </span>
            </label>

            {armError && (
              <div className="p-2.5 rounded bg-rose-950 border border-rose-800 text-rose-300 text-xs">
                {armError}
              </div>
            )}

            {/* Modal Actions */}
            <div className="flex justify-end items-center gap-3 pt-2">
              <button
                type="button"
                onClick={() => setSelectedExperiment(null)}
                className="px-4 py-2 bg-industrial-800 hover:bg-industrial-700 text-zinc-300 rounded text-xs transition"
              >
                Abort / Cancel
              </button>
              <button
                type="button"
                disabled={!armConfirmed || approveMutation.isPending}
                onClick={() => approveMutation.mutate(selectedExperiment.index)}
                className="px-5 py-2 bg-rose-600 hover:bg-rose-500 disabled:opacity-40 text-white font-bold rounded text-xs transition flex items-center gap-2 shadow-lg"
              >
                {approveMutation.isPending ? (
                  <RotateCcw className="w-4 h-4 animate-spin" />
                ) : (
                  <Play className="w-4 h-4 fill-current" />
                )}
                <span>CONFIRM & INJECT</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
