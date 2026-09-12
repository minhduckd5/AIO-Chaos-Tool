import React from 'react';
import { X, Trash2, Radio } from 'lucide-react';
import { StreamItem } from '../hooks/useEventStream';

interface EventDrawerProps {
  open: boolean;
  onClose: () => void;
  events: StreamItem[];
  onClear: () => void;
  connected: boolean;
}

export const EventDrawer: React.FC<EventDrawerProps> = ({
  open,
  onClose,
  events,
  onClear,
  connected,
}) => {
  if (!open) return null;

  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-industrial-900 border-l border-industrial-800 shadow-2xl z-40 flex flex-col font-mono">
      {/* Header */}
      <div className="h-14 border-b border-industrial-800 px-4 flex items-center justify-between bg-industrial-950">
        <div className="flex items-center gap-2">
          <Radio
            className={`w-4 h-4 ${
              connected ? 'text-emerald-400 animate-pulse' : 'text-zinc-500'
            }`}
          />
          <span className="text-xs font-semibold text-zinc-200 uppercase tracking-wider">
            Live Event Stream
          </span>
          <span className="text-[10px] px-1.5 py-0.2 rounded bg-industrial-800 text-zinc-400">
            {events.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onClear}
            title="Clear event logs"
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-industrial-800 rounded transition"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-industrial-800 rounded transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Events List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 text-xs">
        {events.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-zinc-500 italic text-[11px]">
            No stream events received yet...
          </div>
        ) : (
          events.map((ev) => (
            <div
              key={ev.id}
              className="p-2.5 rounded bg-industrial-950 border border-industrial-800 space-y-1 hover:border-industrial-700 transition"
            >
              <div className="flex items-center justify-between text-[10px] text-zinc-500">
                <span className="text-cyan-400 font-semibold uppercase">{ev.type}</span>
                <span>{ev.timestamp}</span>
              </div>

              {ev.state && (
                <div className="text-[11px] text-zinc-300">
                  State: <span className="text-amber-400 font-bold">{ev.state}</span>
                </div>
              )}

              {ev.run_id && (
                <div className="text-[10px] text-zinc-400 truncate">
                  Run ID: <span className="text-zinc-300">{ev.run_id}</span>
                </div>
              )}

              {ev.outcome && (
                <div className="text-[11px]">
                  Outcome: <span className="text-emerald-400 font-bold">{ev.outcome}</span>
                </div>
              )}

              {ev.raw ? (
                <pre className="text-[10px] text-zinc-400 bg-industrial-900 p-1.5 rounded overflow-x-auto max-h-24">
                  {JSON.stringify(ev.raw, null, 2)}
                </pre>
              ) : null}
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-industrial-800 bg-industrial-950 text-[10px] text-zinc-500 flex justify-between items-center">
        <span>SSE Stream: /v1/events/stream</span>
        <span className={connected ? 'text-emerald-400' : 'text-rose-400'}>
          {connected ? '● LIVE' : '○ DISCONNECTED'}
        </span>
      </div>
    </div>
  );
};
