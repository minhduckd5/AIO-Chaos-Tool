import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { FsmState } from '../types';

export interface StreamItem {
  id: string;
  timestamp: string;
  type: string;
  state?: FsmState;
  run_id?: string;
  outcome?: string;
  raw?: unknown;
}

export function useEventStream() {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<StreamItem[]>([]);
  const queryClient = useQueryClient();
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource('/v1/events/stream');
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
    };

    es.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === 'ping') return;

        const item: StreamItem = {
          id: Math.random().toString(36).substring(2, 9),
          timestamp: new Date().toLocaleTimeString(),
          type: data.type || 'message',
          state: data.state,
          run_id: data.run_id,
          outcome: data.outcome,
          raw: data,
        };

        setEvents((prev) => [item, ...prev.slice(0, 49)]);

        // Invalidate relevant queries when state changes
        if (data.type === 'state_change' || data.type === 'connected') {
          queryClient.invalidateQueries({ queryKey: ['status'] });
          queryClient.invalidateQueries({ queryKey: ['pending'] });
          queryClient.invalidateQueries({ queryKey: ['history'] });
          queryClient.invalidateQueries({ queryKey: ['audit'] });
        }
      } catch (err) {
        console.warn('Failed to parse SSE payload', err);
      }
    };

    es.onerror = () => {
      setConnected(false);
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [queryClient]);

  const clearEvents = () => setEvents([]);

  return { connected, events, clearEvents };
}
