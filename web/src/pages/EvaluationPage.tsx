import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Scale, AlertTriangle, ShieldCheck, FileCode } from 'lucide-react';
import { api } from '../lib/api';

export const EvaluationPage: React.FC = () => {
  const { data: verdict, isLoading, error } = useQuery({
    queryKey: ['last-verdict'],
    queryFn: api.getLastVerdict,
    retry: false,
  });

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto font-mono">
      {/* Header */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Scale className="w-5 h-5 text-blue-400" />
            <h1 className="text-lg font-bold text-zinc-100 tracking-tight">
              EVALUATION & VERDICT ALIGNMENT
            </h1>
          </div>
          <p className="text-xs text-zinc-400 mt-1 max-w-2xl">
            Quantitative chaos experiment outcome verification. Correlates steady-state checks,
            recovery latency, and SLI degradation against CTK expectation thresholds.
          </p>
        </div>
      </div>

      {/* Main Verdict Card */}
      <div className="bg-industrial-900 border border-industrial-800 rounded-lg p-6 space-y-4">
        <h2 className="text-sm font-semibold text-zinc-200 uppercase tracking-wider flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <span>Latest Expectation Verdict</span>
        </h2>

        {isLoading ? (
          <div className="p-12 text-center text-xs text-zinc-500">
            Fetching latest expectation verdict...
          </div>
        ) : error || !verdict ? (
          <div className="p-8 text-center bg-industrial-950 rounded border border-industrial-850 space-y-2">
            <AlertTriangle className="w-6 h-6 text-zinc-600 mx-auto" />
            <div className="text-xs text-zinc-400">No expectation verdict records found.</div>
            <div className="text-[11px] text-zinc-600">
              Run a verified experiment or CTK scenario to generate an alignment report.
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="p-4 bg-industrial-950 border border-industrial-800 rounded flex items-center justify-between">
              <div>
                <span className="text-xs text-zinc-400">Terminal Alignment Verdict:</span>
                <div className="text-xl font-bold text-emerald-400 mt-1">
                  {String(verdict.verdict || verdict.outcome || 'PASS')}
                </div>
              </div>
              <div className="text-right text-xs text-zinc-500">
                <div>Generated: {String(verdict.generated_at || verdict.timestamp || 'Recent')}</div>
              </div>
            </div>

            {/* Verdict Payload Details */}
            <div className="space-y-2">
              <span className="text-xs text-zinc-400 font-semibold flex items-center gap-1.5">
                <FileCode className="w-4 h-4 text-cyan-400" />
                <span>Raw Verdict Manifest:</span>
              </span>
              <pre className="p-4 bg-industrial-950 border border-industrial-800 rounded text-xs text-zinc-300 overflow-x-auto max-h-96">
                {JSON.stringify(verdict, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
