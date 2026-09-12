import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  FlaskConical,
  OctagonAlert,
  Play,
  RotateCcw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  Terminal,
} from 'lucide-react';
import { api } from '../lib/api';
import { ExperimentRun } from '../types';

export const ExperimentsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [filterOutcome, setFilterOutcome] = useState<string>('all');
  const [showDirectHatch, setShowDirectHatch] = useState(false);
  const [directHatchJson, setDirectHatchJson] = useState(`{
  "name": "manual-pod-kill",
  "target": {
    "type": "service",
    "name": "frontend",
    "namespace": "default"
  },
  "faults": [
    {
      "fault_type": "process_kill",
      "duration": "10s"
    }
  ],
  "rollback": true
}`);
  const [hatchError, setHatchError] = useState<string | null>(null);

  // Status query for active experiment
  const { data: status } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 2000,
  });

  // History query
  const { data: historyData, isLoading } = useQuery({
    queryKey: ['history'],
    queryFn: api.getHistory,
    refetchInterval: 4000,
  });

  const runs: ExperimentRun[] = historyData?.runs || [];

  // HALT mutation
  const haltMutation = useMutation({
    mutationFn: () => api.halt(),
    onSuccess: (res) => {
      alert(`HALT triggered successfully. State: ${res.state || 'idle'}`);
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['history'] });
    },
    onError: (err: any) => {
      alert(`HALT failed: ${err.message}`);
    },
  });

  // Direct run mutation
  const directRunMutation = useMutation({
    mutationFn: (experimentObj: unknown) => api.runDirect(experimentObj),
    onSuccess: () => {
      setHatchError(null);
      setShowDirectHatch(false);
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['history'] });
      queryClient.invalidateQueries({ queryKey: ['audit'] });
    },
    onError: (err: any) => {
      setHatchError(err.message || 'Direct experiment execution failed');
    },
  });

  const handleDirectRunSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const parsed = JSON.parse(directHatchJson);
      directRunMutation.mutate(parsed);
    } catch (err: any) {
      setHatchError(`Invalid JSON format: ${err.message}`);
    }
  };

  const isActive = status?.state && status.state !== 'idle' && status.state !== 'pending_approval';

  // Counted filter tabs calculation
  const counts = {
    all: runs.length,
    pass: runs.filter((r) => (r.outcome || '').toUpperCase() === 'PASS').length,
    fail: runs.filter((r) => (r.outcome || '').toUpperCase() === 'FAIL').length,
    partial: runs.filter((r) => (r.outcome || '').toUpperCase() === 'PARTIAL').length,
    halted: runs.filter((r) => r.status === 'HALTED' || (r.outcome || '').toUpperCase() === 'ABORTED').length,
    running: runs.filter((r) => r.status === 'RUNNING').length,
  };

  const filteredRuns = runs.filter((r) => {
    if (filterOutcome === 'all') return true;
    if (filterOutcome === 'pass') return (r.outcome || '').toUpperCase() === 'PASS';
    if (filterOutcome === 'fail') return (r.outcome || '').toUpperCase() === 'FAIL';
    if (filterOutcome === 'partial') return (r.outcome || '').toUpperCase() === 'PARTIAL';
    if (filterOutcome === 'halted') return r.status === 'HALTED' || (r.outcome || '').toUpperCase() === 'ABORTED';
    if (filterOutcome === 'running') return r.status === 'RUNNING';
    return true;
  });

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto font-mono">
      {/* Top Banner & Emergency HALT Button */}
      <div
        className={`border rounded-lg p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 transition-colors ${
          isActive
            ? 'bg-rose-950/40 border-rose-600 shadow-lg shadow-rose-950/50'
            : 'bg-industrial-900 border-industrial-800'
        }`}
      >
        <div>
          <div className="flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-rose-400" />
            <h1 className="text-lg font-bold text-zinc-100 tracking-tight">
              EXPERIMENTS & ACTIVE EXECUTION CONSOLE
            </h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1 max-w-2xl">
            Monitor real-time experiment executions, terminal outcomes, and trigger instantaneous
            zero-delay emergency aborts.
          </p>
        </div>

        {/* Emergency HALT & Operator Direct Hatch */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowDirectHatch(!showDirectHatch)}
            className="flex items-center gap-1.5 px-3 py-2 bg-industrial-950 hover:bg-industrial-850 border border-industrial-700 text-zinc-300 text-xs rounded transition"
          >
            <Terminal className="w-4 h-4 text-cyan-400" />
            <span>Operator Hatch</span>
          </button>

          <button
            onClick={() => {
              if (confirm('EMERGENCY HALT: Immediately abort active injection and trigger rollback?')) {
                haltMutation.mutate();
              }
            }}
            disabled={haltMutation.isPending}
            className="flex items-center gap-2 px-5 py-2 bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs rounded shadow-lg shadow-rose-900/40 transition active:scale-95"
          >
            <OctagonAlert className="w-4 h-4" />
            <span>EMERGENCY HALT</span>
          </button>
        </div>
      </div>

      {/* Operator Direct Hatch Modal / Drawer */}
      {showDirectHatch && (
        <form
          onSubmit={handleDirectRunSubmit}
          className="p-5 bg-industrial-950 border border-industrial-800 rounded-lg space-y-3"
        >
          <div className="flex justify-between items-center">
            <div className="text-xs font-bold text-cyan-400 uppercase tracking-wider flex items-center gap-2">
              <Terminal className="w-4 h-4" />
              <span>Operator Direct Hatch (Bypasses AI Advisor HITL Gate)</span>
            </div>
            <button
              type="button"
              onClick={() => setShowDirectHatch(false)}
              className="text-zinc-500 hover:text-zinc-300 text-xs"
            >
              Close
            </button>
          </div>

          <p className="text-[11px] text-zinc-400">
            Executes a validated experiment payload directly. Emits an audit event tagged as{' '}
            <code className="text-amber-400 font-mono">path_used="operator_direct"</code> and verifies blast radius policy.
          </p>

          <textarea
            rows={8}
            value={directHatchJson}
            onChange={(e) => setDirectHatchJson(e.target.value)}
            className="w-full bg-industrial-900 border border-industrial-800 rounded p-3 text-xs text-zinc-200 font-mono focus:outline-none focus:border-cyan-500"
          />

          {hatchError && (
            <div className="p-2.5 bg-rose-950 border border-rose-800 text-rose-300 text-xs rounded">
              {hatchError}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <button
              type="submit"
              disabled={directRunMutation.isPending}
              className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white font-bold rounded text-xs transition flex items-center gap-2"
            >
              {directRunMutation.isPending ? (
                <RotateCcw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5 fill-current" />
              )}
              <span>Execute via Direct Hatch</span>
            </button>
          </div>
        </form>
      )}

      {/* Filter Tabs & History Table */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg overflow-hidden">
        {/* Counted Filter Tabs */}
        <div className="p-3 border-b border-industrial-800 bg-industrial-950/80 flex flex-wrap gap-2 text-xs">
          {[
            { id: 'all', label: 'All', count: counts.all },
            { id: 'pass', label: 'PASS', count: counts.pass },
            { id: 'fail', label: 'FAIL', count: counts.fail },
            { id: 'partial', label: 'PARTIAL', count: counts.partial },
            { id: 'halted', label: 'HALTED', count: counts.halted },
            { id: 'running', label: 'RUNNING', count: counts.running },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setFilterOutcome(tab.id)}
              className={`px-3 py-1.5 rounded transition flex items-center gap-1.5 text-xs font-semibold ${
                filterOutcome === tab.id
                  ? 'bg-industrial-800 text-zinc-100 border border-zinc-700'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-industrial-900'
              }`}
            >
              <span>{tab.label}</span>
              <span
                className={`text-[10px] px-1.5 py-0.2 rounded-full ${
                  filterOutcome === tab.id ? 'bg-industrial-950 text-cyan-400' : 'bg-industrial-900 text-zinc-600'
                }`}
              >
                {tab.count}
              </span>
            </button>
          ))}
        </div>

        {/* Runs Table */}
        {isLoading ? (
          <div className="p-12 text-center text-xs text-zinc-500">
            Loading run history...
          </div>
        ) : filteredRuns.length === 0 ? (
          <div className="p-12 text-center text-xs text-zinc-500">
            No experiment runs found matching filter '{filterOutcome.toUpperCase()}'.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-industrial-800 bg-industrial-950 text-zinc-400 text-[11px] uppercase tracking-wider">
                  <th className="py-3 px-4 font-medium">Run ID & Name</th>
                  <th className="py-3 px-4 font-medium">Actor & Path</th>
                  <th className="py-3 px-4 font-medium">Timing / Duration</th>
                  <th className="py-3 px-4 font-medium">Outcome Status</th>
                  <th className="py-3 px-4 font-medium">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-industrial-800">
                {filteredRuns.map((run) => {
                  const outcome = (run.outcome || '').toUpperCase();
                  return (
                    <tr key={run.run_id} className="hover:bg-industrial-850/50 transition-colors">
                      {/* Run ID & Name */}
                      <td className="py-3 px-4 align-top">
                        <div className="font-semibold text-zinc-100">{run.name}</div>
                        <div className="text-[10px] text-zinc-500 mt-0.5">
                          ID: <span className="text-zinc-400">{run.run_id.substring(0, 8)}...</span>
                        </div>
                      </td>

                      {/* Actor & Path */}
                      <td className="py-3 px-4 align-top whitespace-nowrap">
                        <div className="text-zinc-300 font-medium">{run.actor}</div>
                        <div className="text-[10px] text-zinc-500 mt-0.5">
                          path:{' '}
                          <span
                            className={
                              run.path_used === 'operator_direct'
                                ? 'text-amber-400 font-semibold'
                                : 'text-cyan-400'
                            }
                          >
                            {run.path_used}
                          </span>
                        </div>
                      </td>

                      {/* Timing */}
                      <td className="py-3 px-4 align-top text-zinc-400 whitespace-nowrap">
                        <div className="flex items-center gap-1 text-[11px]">
                          <Clock className="w-3 h-3 text-zinc-500" />
                          <span>{new Date(run.started_at).toLocaleTimeString()}</span>
                        </div>
                        {run.duration_seconds !== null && run.duration_seconds !== undefined && (
                          <div className="text-[10px] text-zinc-500 mt-0.5">
                            {run.duration_seconds}s total
                          </div>
                        )}
                      </td>

                      {/* Outcome Badge */}
                      <td className="py-3 px-4 align-top whitespace-nowrap">
                        {run.status === 'RUNNING' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 text-[10px] animate-pulse">
                            <RotateCcw className="w-3 h-3 animate-spin" />
                            RUNNING
                          </span>
                        ) : outcome === 'PASS' || outcome === 'SUCCESS' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] font-bold">
                            <CheckCircle2 className="w-3 h-3" />
                            PASS
                          </span>
                        ) : outcome === 'FAIL' || outcome === 'FAILURE' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-800 text-[10px] font-bold">
                            <XCircle className="w-3 h-3" />
                            FAIL
                          </span>
                        ) : outcome === 'PARTIAL' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800 text-[10px] font-bold">
                            <AlertTriangle className="w-3 h-3" />
                            PARTIAL
                          </span>
                        ) : run.status === 'HALTED' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-orange-950 text-orange-400 border border-orange-800 text-[10px] font-bold">
                            <OctagonAlert className="w-3 h-3" />
                            HALTED
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]">
                            {outcome || 'UNKNOWN'}
                          </span>
                        )}
                      </td>

                      {/* Notes */}
                      <td className="py-3 px-4 align-top text-zinc-400 text-[11px] max-w-xs">
                        {run.notes || <span className="text-zinc-600 italic">None recorded</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
