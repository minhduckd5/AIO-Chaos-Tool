import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Layers,
  PlusCircle,
  AlertTriangle,
  CheckCircle2,
  Filter,
  Tag,
  RotateCcw,
} from 'lucide-react';
import { api } from '../lib/api';

export const CatalogPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [selectedArch, setSelectedArch] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');

  // Target customization state
  const [targetOverrides, setTargetOverrides] = useState<
    Record<string, { target?: string; namespace?: string }>
  >({});

  // 409 Conflict replacement modal
  const [pendingReplace, setPendingReplace] = useState<{
    name: string;
    override?: { target?: string; namespace?: string };
    message: string;
  } | null>(null);

  const [feedback, setFeedback] = useState<string | null>(null);

  const { data: catalogData, isLoading } = useQuery({
    queryKey: ['catalog'],
    queryFn: api.getCatalog,
  });

  const scenarios = catalogData?.scenarios || [];

  const queueMutation = useMutation({
    mutationFn: ({
      name,
      force,
      override,
    }: {
      name: string;
      force: boolean;
      override?: { target?: string; namespace?: string };
    }) => api.queueCatalog(name, force, override),
    onSuccess: (res) => {
      setPendingReplace(null);
      setFeedback(`Successfully staged '${res.name}' in HITL approval queue.`);
      queryClient.invalidateQueries({ queryKey: ['pending'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
      setTimeout(() => setFeedback(null), 4000);
    },
    onError: (err: any, variables) => {
      if (err.status === 409) {
        setPendingReplace({
          name: variables.name,
          override: variables.override,
          message: err.message || 'Queue contains unapproved scenarios.',
        });
      } else {
        alert(`Failed to queue scenario: ${err.message}`);
      }
    },
  });

  const architectures = [
    'all',
    ...Array.from(new Set(scenarios.map((s) => s.architecture))),
  ];

  const filteredScenarios = scenarios.filter((s) => {
    const matchesArch = selectedArch === 'all' || s.architecture === selectedArch;
    const matchesSearch =
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()));
    return matchesArch && matchesSearch;
  });

  const handleTargetChange = (name: string, field: 'target' | 'namespace', value: string) => {
    setTargetOverrides((prev) => ({
      ...prev,
      [name]: {
        ...prev[name],
        [field]: value,
      },
    }));
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto font-mono">
      {/* Top Header */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-purple-400" />
            <h1 className="text-lg font-bold text-zinc-100 tracking-tight">
              SCENARIO CATALOG
            </h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1 max-w-2xl">
            Pre-defined, verified chaos scenarios mapped to standard enterprise architectures.
            Customise target selectors and stage directly into the approval queue.
          </p>
        </div>

        {/* Filter controls */}
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Search scenarios or tags..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="bg-industrial-950 border border-industrial-700 text-xs text-zinc-200 px-3 py-1.5 rounded focus:outline-none focus:border-purple-500 w-52"
          />

          <div className="flex items-center gap-1.5 text-xs text-zinc-400">
            <Filter className="w-3.5 h-3.5" />
            <select
              value={selectedArch}
              onChange={(e) => setSelectedArch(e.target.value)}
              className="bg-industrial-950 border border-industrial-700 text-zinc-200 px-2.5 py-1.5 rounded focus:outline-none focus:border-purple-500"
            >
              {architectures.map((arch) => (
                <option key={arch} value={arch}>
                  {arch.toUpperCase()}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {feedback && (
        <div className="p-3 bg-emerald-950/80 border border-emerald-700 text-emerald-300 rounded text-xs flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>{feedback}</span>
        </div>
      )}

      {/* Scenario Grid */}
      {isLoading ? (
        <div className="p-12 text-center text-xs text-zinc-500">
          Loading scenario catalog...
        </div>
      ) : filteredScenarios.length === 0 ? (
        <div className="p-12 text-center text-zinc-500 text-xs">
          No scenarios matching current filters.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredScenarios.map((sc) => {
            const override = targetOverrides[sc.name] || {};
            return (
              <div
                key={sc.name}
                className="bg-industrial-900 border border-industrial-800 rounded-lg p-4 flex flex-col justify-between hover:border-industrial-700 transition space-y-3"
              >
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-semibold text-zinc-100">{sc.name}</h3>
                    <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-purple-950 text-purple-300 border border-purple-800 shrink-0">
                      {sc.architecture}
                    </span>
                  </div>

                  <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
                    {sc.description}
                  </p>

                  {/* Fault Badge */}
                  <div className="mt-3 flex items-center gap-1.5">
                    <span className="text-[10px] text-zinc-500">Fault:</span>
                    <span className="text-[11px] px-2 py-0.5 rounded bg-rose-950/60 text-rose-300 border border-rose-900 font-medium">
                      {sc.fault_type}
                    </span>
                  </div>

                  {/* Tags */}
                  {sc.tags && sc.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {sc.tags.map((t) => (
                        <span
                          key={t}
                          className="inline-flex items-center gap-1 text-[10px] text-zinc-500 bg-industrial-950 px-1.5 py-0.5 rounded border border-industrial-800"
                        >
                          <Tag className="w-2.5 h-2.5" />
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Target Override Inputs & Stage Button */}
                <div className="pt-3 border-t border-industrial-800 space-y-2 text-xs">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-[10px] text-zinc-500">Target Service</label>
                      <input
                        type="text"
                        placeholder="e.g. checkoutservice"
                        value={override.target || ''}
                        onChange={(e) => handleTargetChange(sc.name, 'target', e.target.value)}
                        className="w-full bg-industrial-950 border border-industrial-800 px-2 py-1 rounded text-zinc-200 text-xs focus:outline-none focus:border-purple-500 mt-0.5"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-zinc-500">Namespace</label>
                      <input
                        type="text"
                        placeholder="default"
                        value={override.namespace || ''}
                        onChange={(e) => handleTargetChange(sc.name, 'namespace', e.target.value)}
                        className="w-full bg-industrial-950 border border-industrial-800 px-2 py-1 rounded text-zinc-200 text-xs focus:outline-none focus:border-purple-500 mt-0.5"
                      />
                    </div>
                  </div>

                  <button
                    onClick={() =>
                      queueMutation.mutate({
                        name: sc.name,
                        force: false,
                        override: override.target || override.namespace ? override : undefined,
                      })
                    }
                    disabled={queueMutation.isPending}
                    className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 bg-purple-700 hover:bg-purple-600 text-white font-medium rounded transition"
                  >
                    <PlusCircle className="w-3.5 h-3.5" />
                    <span>Stage for HITL Approval</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 409 Conflict Replacement Modal */}
      {pendingReplace && (
        <div className="fixed inset-0 bg-industrial-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-industrial-900 border-2 border-amber-500 rounded-lg max-w-md w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center gap-3 text-amber-400">
              <AlertTriangle className="w-6 h-6 shrink-0" />
              <div>
                <h3 className="text-sm font-bold uppercase tracking-wider text-zinc-100">
                  Unapproved Queue Conflict (409)
                </h3>
                <p className="text-xs text-amber-300">
                  Existing scenario(s) awaiting operator review
                </p>
              </div>
            </div>

            <div className="p-3 bg-industrial-950 border border-industrial-800 rounded text-xs text-zinc-300">
              {pendingReplace.message}
            </div>

            <p className="text-xs text-zinc-400">
              Do you want to discard the unapproved scenarios and stage{' '}
              <span className="text-purple-300 font-bold">{pendingReplace.name}</span>?
            </p>

            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setPendingReplace(null)}
                className="px-3 py-1.5 bg-industrial-800 hover:bg-industrial-700 text-zinc-300 rounded text-xs transition"
              >
                Cancel
              </button>
              <button
                onClick={() =>
                  queueMutation.mutate({
                    name: pendingReplace.name,
                    force: true,
                    override: pendingReplace.override,
                  })
                }
                className="px-4 py-1.5 bg-amber-600 hover:bg-amber-500 text-industrial-950 font-bold rounded text-xs transition flex items-center gap-1.5"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Replace Queue (?force=true)</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
