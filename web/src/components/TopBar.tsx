import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Radio,
  UserCheck,
  Terminal,
} from 'lucide-react';
import { api, getStoredOperator, setStoredOperator } from '../lib/api';
import { FsmState } from '../types';

interface TopBarProps {
  onToggleDrawer: () => void;
  streamConnected: boolean;
  drawerOpen: boolean;
}

export const TopBar: React.FC<TopBarProps> = ({
  onToggleDrawer,
  streamConnected,
  drawerOpen,
}) => {
  const [operator, setOperator] = useState(getStoredOperator());
  const [editingOperator, setEditingOperator] = useState(false);
  const [tempOperator, setTempOperator] = useState(operator);

  const { data: status } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 2000,
  });

  const { data: history } = useQuery({
    queryKey: ['history'],
    queryFn: api.getHistory,
    refetchInterval: 5000,
  });

  const { data: telemetryPing } = useQuery({
    queryKey: ['telemetry-check'],
    queryFn: () => api.checkTelemetry(),
    refetchInterval: 15000,
  });

  const handleSaveOperator = (e: React.FormEvent) => {
    e.preventDefault();
    setStoredOperator(tempOperator);
    setOperator(tempOperator);
    setEditingOperator(false);
  };

  const state: FsmState = status?.state || 'idle';
  const lastRun = history?.runs?.[0];
  const lastOutcome = lastRun?.outcome;

  const getStateStyle = (s: FsmState) => {
    switch (s) {
      case 'idle':
        return 'bg-zinc-800 text-zinc-300 border-zinc-700';
      case 'pending_approval':
        return 'bg-amber-950/80 text-amber-300 border-amber-600 animate-pulse';
      case 'injecting':
        return 'bg-rose-950/80 text-rose-300 border-rose-600 animate-pulse font-semibold';
      case 'steady_state_check':
        return 'bg-blue-950/80 text-blue-300 border-blue-600 animate-pulse';
      case 'verifying':
        return 'bg-purple-950/80 text-purple-300 border-purple-600 animate-pulse';
      case 'rollback':
        return 'bg-orange-950/80 text-orange-300 border-orange-600 animate-pulse font-semibold';
      default:
        return 'bg-zinc-800 text-zinc-400 border-zinc-700';
    }
  };

  return (
    <header className="h-14 border-b border-industrial-800 bg-industrial-900/90 backdrop-blur px-4 flex items-center justify-between sticky top-0 z-30">
      {/* Brand & Honesty Pill */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded bg-cyan-600/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400">
            <Activity className="w-4 h-4" />
          </div>
          <span className="font-bold tracking-tight text-zinc-100 text-base font-mono">
            CHAOS<span className="text-cyan-400">GEN</span>
          </span>
          <span className="text-[10px] uppercase tracking-widest px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700 font-mono">
            v0.2.0
          </span>
        </div>

        {/* Compact Honesty Pill */}
        <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-industrial-950 border border-industrial-800 text-xs font-mono shadow-inner">
          {/* FSM state */}
          <div className="flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${
                state === 'idle'
                  ? 'bg-zinc-500'
                  : state === 'pending_approval'
                  ? 'bg-amber-400 animate-ping'
                  : state === 'injecting'
                  ? 'bg-rose-500 animate-ping'
                  : 'bg-cyan-400 animate-pulse'
              }`}
            />
            <span
              className={`px-2 py-0.5 rounded border text-[11px] uppercase tracking-wider ${getStateStyle(
                state
              )}`}
            >
              {state.replace(/_/g, ' ')}
            </span>
          </div>

          <span className="text-zinc-600">|</span>

          {/* Last Outcome */}
          <div className="flex items-center gap-1 text-[11px]">
            <span className="text-zinc-400">LAST:</span>
            {lastOutcome ? (
              <span
                className={`font-semibold px-1.5 py-0.2 rounded ${
                  lastOutcome === 'PASS' || lastOutcome === 'success'
                    ? 'text-emerald-400 bg-emerald-950/60 border border-emerald-800'
                    : lastOutcome === 'FAIL' || lastOutcome === 'failure'
                    ? 'text-rose-400 bg-rose-950/60 border border-rose-800'
                    : 'text-amber-400 bg-amber-950/60 border border-amber-800'
                }`}
              >
                {lastOutcome}
              </span>
            ) : (
              <span className="text-zinc-500 italic">none</span>
            )}
          </div>

          <span className="text-zinc-600">|</span>

          {/* Telemetry Links */}
          <div className="flex items-center gap-2 text-[11px]">
            <div
              className="flex items-center gap-1 cursor-default"
              title={
                telemetryPing?.prometheus?.connected
                  ? `Prometheus Connected: ${telemetryPing.prometheus.url || 'healthy'}`
                  : `Prometheus Disconnected: ${telemetryPing?.prometheus?.message || 'offline'}`
              }
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  telemetryPing?.prometheus?.connected ? 'bg-emerald-400' : 'bg-rose-500'
                }`}
              />
              <span className="text-zinc-400">PROM</span>
            </div>
            <div
              className="flex items-center gap-1 cursor-default"
              title={
                telemetryPing?.loki?.connected
                  ? `Loki Connected: ${telemetryPing.loki.url || 'healthy'}`
                  : `Loki Disconnected: ${telemetryPing?.loki?.message || 'offline'}`
              }
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  telemetryPing?.loki?.connected ? 'bg-emerald-400' : 'bg-rose-500'
                }`}
              />
              <span className="text-zinc-400">LOKI</span>
            </div>
          </div>
        </div>
      </div>

      {/* Operator Identity & Stream Drawer Toggle */}
      <div className="flex items-center gap-3">
        {/* Operator Badge */}
        {editingOperator ? (
          <form onSubmit={handleSaveOperator} className="flex items-center gap-1">
            <input
              type="text"
              value={tempOperator}
              onChange={(e) => setTempOperator(e.target.value)}
              placeholder="Operator name"
              autoFocus
              className="bg-industrial-950 border border-cyan-500 text-xs text-zinc-100 px-2 py-1 rounded font-mono focus:outline-none"
            />
            <button
              type="submit"
              className="px-2 py-1 bg-cyan-600 text-zinc-100 text-xs rounded hover:bg-cyan-500 font-mono"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditingOperator(false)}
              className="px-2 py-1 bg-zinc-800 text-zinc-400 text-xs rounded hover:bg-zinc-700 font-mono"
            >
              Cancel
            </button>
          </form>
        ) : (
          <button
            onClick={() => {
              setTempOperator(operator);
              setEditingOperator(true);
            }}
            title="Click to edit operator identity"
            className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-industrial-850 hover:bg-industrial-800 border border-industrial-700 text-xs text-zinc-300 font-mono transition"
          >
            <UserCheck className="w-3.5 h-3.5 text-cyan-400" />
            <span className="text-zinc-400">OPERATOR:</span>
            <span className="text-cyan-300 font-medium">{operator}</span>
          </button>
        )}

        {/* Live SSE Drawer Toggle */}
        <button
          onClick={onToggleDrawer}
          className={`flex items-center gap-2 px-3 py-1 rounded border text-xs font-mono transition ${
            drawerOpen
              ? 'bg-cyan-950 border-cyan-600 text-cyan-300'
              : 'bg-industrial-850 border-industrial-700 text-zinc-300 hover:bg-industrial-800'
          }`}
        >
          <Radio
            className={`w-3.5 h-3.5 ${
              streamConnected ? 'text-emerald-400 animate-pulse' : 'text-zinc-500'
            }`}
          />
          <Terminal className="w-3.5 h-3.5" />
          <span>EVENTS</span>
        </button>
      </div>
    </header>
  );
};
