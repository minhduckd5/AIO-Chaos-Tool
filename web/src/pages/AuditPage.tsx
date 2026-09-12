import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ScrollText,
  FileText,
  X,
  ChevronRight,
} from 'lucide-react';
import { api } from '../lib/api';
import { AuditEvent } from '../types';

export const AuditPage: React.FC = () => {
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const [activeFilter, setActiveFilter] = useState<string>('all');

  const { data: summaryData } = useQuery({
    queryKey: ['audit-summary'],
    queryFn: api.getAuditSummary,
    refetchInterval: 5000,
  });

  const { data: auditData, isLoading } = useQuery({
    queryKey: ['audit-recent'],
    queryFn: () => api.getAuditRecent(100),
    refetchInterval: 5000,
  });

  const events: AuditEvent[] = auditData?.events || [];
  const summary = summaryData || {
    all: 0,
    total: 0,
    pass: 0,
    fail: 0,
    partial: 0,
    no_target: 0,
    inconclusive: 0,
  };

  const filteredEvents = events.filter((ev) => {
    if (activeFilter === 'all') return true;
    const outcome = (ev.outcome || '').toLowerCase();
    const notes = (ev.notes || '').toLowerCase();
    if (activeFilter === 'pass') return outcome === 'success' || notes.includes('pass');
    if (activeFilter === 'fail') return outcome === 'failure' || notes.includes('fail');
    if (activeFilter === 'partial') return outcome === 'partial' || outcome === 'aborted' || notes.includes('partial');
    if (activeFilter === 'no_target') return notes.includes('no_target');
    if (activeFilter === 'inconclusive') return notes.includes('inconclusive');
    return true;
  });

  const getEventTypeColor = (type: string) => {
    switch (type) {
      case 'queued':
        return 'text-cyan-400 bg-cyan-950/70 border-cyan-800';
      case 'approved':
        return 'text-emerald-400 bg-emerald-950/70 border-emerald-800';
      case 'rejected':
        return 'text-zinc-400 bg-zinc-900 border-zinc-700';
      case 'inject_started':
        return 'text-rose-400 bg-rose-950/70 border-rose-800 font-bold';
      case 'inject_finished':
        return 'text-purple-400 bg-purple-950/70 border-purple-800 font-semibold';
      case 'rollback':
        return 'text-amber-400 bg-amber-950/70 border-amber-800';
      case 'hatch_used':
        return 'text-orange-400 bg-orange-950/70 border-orange-800 font-bold';
      case 'orphan_sweep':
        return 'text-blue-400 bg-blue-950/70 border-blue-800';
      default:
        return 'text-zinc-400 bg-zinc-900 border-zinc-800';
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto font-mono">
      {/* Header */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div className="flex items-center gap-2">
            <ScrollText className="w-5 h-5 text-emerald-400" />
            <h1 className="text-lg font-bold text-zinc-100 tracking-tight">
              AUDIT TRAIL & INCIDENT TIMELINE
            </h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1 max-w-2xl">
            Append-only JSONL tamper-evident audit log. Tracks all operator approvals, AI generation paths,
            direct operator hatches, and blast radius compliance.
          </p>
        </div>
      </div>

      {/* Counted Filter Tabs */}
      <div className="flex flex-wrap gap-2 text-xs">
        {[
          { id: 'all', label: 'All Events', count: summary.all || summary.total || events.length },
          { id: 'pass', label: 'PASS', count: summary.pass },
          { id: 'fail', label: 'FAIL', count: summary.fail },
          { id: 'partial', label: 'PARTIAL', count: summary.partial },
          { id: 'no_target', label: 'NO_TARGET', count: summary.no_target },
          { id: 'inconclusive', label: 'INCONCLUSIVE', count: summary.inconclusive },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveFilter(tab.id)}
            className={`px-3 py-1.5 rounded transition flex items-center gap-1.5 text-xs font-semibold ${
              activeFilter === tab.id
                ? 'bg-industrial-800 text-zinc-100 border border-zinc-700'
                : 'text-zinc-500 hover:text-zinc-300 hover:bg-industrial-900 border border-transparent'
            }`}
          >
            <span>{tab.label}</span>
            <span
              className={`text-[10px] px-1.5 py-0.2 rounded-full ${
                activeFilter === tab.id ? 'bg-industrial-950 text-cyan-400' : 'bg-industrial-900 text-zinc-600'
              }`}
            >
              {tab.count}
            </span>
          </button>
        ))}
      </div>

      {/* Events Timeline / Table */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-xs text-zinc-500">
            Loading audit timeline...
          </div>
        ) : filteredEvents.length === 0 ? (
          <div className="p-12 text-center text-zinc-500 text-xs">
            No audit records matching filter '{activeFilter.toUpperCase()}'.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-industrial-800 bg-industrial-950 text-zinc-400 text-[11px] uppercase tracking-wider">
                  <th className="py-3 px-4 font-medium">Timestamp</th>
                  <th className="py-3 px-4 font-medium">Event Type</th>
                  <th className="py-3 px-4 font-medium">Actor & Path</th>
                  <th className="py-3 px-4 font-medium">Experiment</th>
                  <th className="py-3 px-4 font-medium">Outcome</th>
                  <th className="py-3 px-4 font-medium text-right">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-industrial-800">
                {filteredEvents.map((ev) => (
                  <tr
                    key={ev.event_id}
                    onClick={() => setSelectedEvent(ev)}
                    className="hover:bg-industrial-850/50 transition cursor-pointer"
                  >
                    {/* Timestamp */}
                    <td className="py-3 px-4 text-zinc-400 whitespace-nowrap text-[11px]">
                      {new Date(ev.timestamp).toLocaleString()}
                    </td>

                    {/* Event Type */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <span
                        className={`px-2 py-0.5 rounded border text-[10px] uppercase font-mono ${getEventTypeColor(
                          ev.event_type
                        )}`}
                      >
                        {ev.event_type.replace(/_/g, ' ')}
                      </span>
                    </td>

                    {/* Actor & Path */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <div className="text-zinc-200 font-medium">{ev.actor}</div>
                      <div className="text-[10px] text-zinc-500 mt-0.5">
                        {ev.path_used}
                      </div>
                    </td>

                    {/* Experiment */}
                    <td className="py-3 px-4 text-zinc-300">
                      {ev.experiment_name || (ev.notes && (ev.path_used === 'module_direct' || ev.event_type.startsWith('inject_') || ev.event_type === 'hatch_used') ? ev.notes : null) || <span className="text-zinc-600 italic">—</span>}
                    </td>

                    {/* Outcome */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      {ev.outcome ? (
                        <span
                          className={`font-semibold text-[11px] ${
                            ev.outcome.toLowerCase() === 'success' || ev.outcome.toUpperCase() === 'PASS'
                              ? 'text-emerald-400'
                              : ev.outcome.toLowerCase() === 'failure' || ev.outcome.toUpperCase() === 'FAIL'
                              ? 'text-rose-400'
                              : 'text-amber-400'
                          }`}
                        >
                          {ev.outcome.toUpperCase()}
                        </span>
                      ) : (
                        <span className="text-zinc-600 italic">—</span>
                      )}
                    </td>

                    {/* Action */}
                    <td className="py-3 px-4 text-right">
                      <span className="text-zinc-500 hover:text-cyan-400 inline-flex items-center gap-1 text-[11px]">
                        <span>View</span>
                        <ChevronRight className="w-3.5 h-3.5" />
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 2-Column Detail Modal: Narrative vs Facts */}
      {selectedEvent && (
        <div className="fixed inset-0 bg-industrial-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-industrial-900 border border-industrial-800 rounded-lg max-w-4xl w-full max-h-[90vh] flex flex-col shadow-2xl font-mono">
            {/* Modal Header */}
            <div className="p-4 border-b border-industrial-800 flex justify-between items-center bg-industrial-950">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-cyan-400" />
                <h3 className="text-sm font-bold text-zinc-100 uppercase tracking-wider">
                  Audit Incident Record — {selectedEvent.event_type.toUpperCase()}
                </h3>
              </div>
              <button
                onClick={() => setSelectedEvent(null)}
                className="p-1 text-zinc-400 hover:text-zinc-200 rounded"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* 2-Column Body */}
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6 overflow-y-auto text-xs">
              {/* Column 1: Narrative & Notes */}
              <div className="space-y-4">
                <div className="text-[11px] font-bold text-zinc-400 uppercase tracking-wider border-b border-industrial-800 pb-1">
                  1. Incident Narrative & Notes
                </div>

                <div className="space-y-3">
                  <div>
                    <span className="text-zinc-500">Experiment Name:</span>
                    <div className="text-zinc-100 font-semibold text-sm mt-0.5">
                      {selectedEvent.experiment_name || 'N/A'}
                    </div>
                  </div>

                  <div>
                    <span className="text-zinc-500">Outcome Evaluation:</span>
                    <div className="mt-0.5 font-bold text-sm text-cyan-300">
                      {selectedEvent.outcome || 'N/A'}
                    </div>
                  </div>

                  <div>
                    <span className="text-zinc-500">Execution Notes:</span>
                    <div className="p-3 bg-industrial-950 border border-industrial-800 rounded text-zinc-300 leading-relaxed text-[11px] mt-1 whitespace-pre-wrap">
                      {selectedEvent.notes || 'No detailed narrative recorded.'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Column 2: Hard Facts Metadata */}
              <div className="space-y-4">
                <div className="text-[11px] font-bold text-zinc-400 uppercase tracking-wider border-b border-industrial-800 pb-1">
                  2. Provenance & Hard Facts
                </div>

                <div className="space-y-2 text-[11px]">
                  <div className="flex justify-between py-1 border-b border-industrial-850">
                    <span className="text-zinc-500">Event ID</span>
                    <span className="text-zinc-300 select-all">{selectedEvent.event_id}</span>
                  </div>

                  <div className="flex justify-between py-1 border-b border-industrial-850">
                    <span className="text-zinc-500">Run ID</span>
                    <span className="text-zinc-300 select-all">{selectedEvent.run_id || '—'}</span>
                  </div>

                  <div className="flex justify-between py-1 border-b border-industrial-850">
                    <span className="text-zinc-500">Timestamp</span>
                    <span className="text-zinc-300">{selectedEvent.timestamp}</span>
                  </div>

                  <div className="flex justify-between py-1 border-b border-industrial-850">
                    <span className="text-zinc-500">Actor Identity</span>
                    <span className="text-cyan-400 font-semibold">{selectedEvent.actor}</span>
                  </div>

                  <div className="flex justify-between py-1 border-b border-industrial-850">
                    <span className="text-zinc-500">Execution Path</span>
                    <span className="text-purple-400">{selectedEvent.path_used}</span>
                  </div>

                  {selectedEvent.blast_radius_ref && (
                    <div className="pt-2">
                      <span className="text-zinc-500 font-semibold">Blast Radius Ref:</span>
                      <pre className="mt-1 p-2 bg-industrial-950 border border-industrial-800 rounded text-[10px] text-zinc-400 overflow-x-auto">
                        {JSON.stringify(selectedEvent.blast_radius_ref, null, 2)}
                      </pre>
                    </div>
                  )}

                  {selectedEvent.criteria_ref && (
                    <div className="pt-2">
                      <span className="text-zinc-500 font-semibold">Criteria Ref:</span>
                      <pre className="mt-1 p-2 bg-industrial-950 border border-industrial-800 rounded text-[10px] text-zinc-400 overflow-x-auto">
                        {JSON.stringify(selectedEvent.criteria_ref, null, 2)}
                      </pre>
                    </div>
                  )}

                  {selectedEvent.target_cluster_context && (
                    <div className="pt-2">
                      <span className="text-zinc-500 font-semibold">Target Cluster Context:</span>
                      <pre className="mt-1 p-2 bg-industrial-950 border border-industrial-800 rounded text-[10px] text-zinc-400 overflow-x-auto">
                        {JSON.stringify(selectedEvent.target_cluster_context, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="p-4 border-t border-industrial-800 bg-industrial-950 flex justify-end">
              <button
                onClick={() => setSelectedEvent(null)}
                className="px-4 py-1.5 bg-industrial-800 hover:bg-industrial-700 text-zinc-300 rounded text-xs transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
