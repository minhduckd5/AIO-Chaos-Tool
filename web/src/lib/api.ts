import {
  AuditSummary,
  AuditEvent,
  CatalogScenario,
  ExperimentRun,
  PendingResponse,
  StatusResponse,
  TelemetryCheckResponse,
} from '../types';

const OPERATOR_STORAGE_KEY = 'chaosgen_operator_name';

export function getStoredOperator(): string {
  return localStorage.getItem(OPERATOR_STORAGE_KEY) || 'operator';
}

export function setStoredOperator(name: string): void {
  const clean = name.trim() || 'operator';
  localStorage.setItem(OPERATOR_STORAGE_KEY, clean);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  const method = (options.method || 'GET').toUpperCase();

  // Attach operator header for mutating operations
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    if (!headers.has('X-Operator-Name')) {
      headers.set('X-Operator-Name', getStoredOperator());
    }
  }

  if (!headers.has('Content-Type') && options.body && typeof options.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(path, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let errorDetail = `HTTP ${res.status} ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson && errJson.detail) {
        errorDetail = errJson.detail;
      }
    } catch {
      // keep default message
    }
    const err = new Error(errorDetail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }

  return res.json() as Promise<T>;
}

export const api = {
  // Status & Health
  getStatus: () => request<StatusResponse>('/v1/status'),
  getHealth: () => request<{ status: string }>('/health'),

  // Pending HITL Gate
  getPending: () => request<PendingResponse>('/v1/pending'),
  approvePending: (index: number) =>
    request<{ ran: boolean; outcome?: string; reason?: string }>(`/v1/pending/${index}/approve`, {
      method: 'POST',
    }),
  rejectPending: (index: number) =>
    request<{ ok: boolean; remaining: number; state: string }>(`/v1/pending/${index}/reject`, {
      method: 'POST',
    }),
  rejectAllPending: () =>
    request<{ ok: boolean; state: string }>('/v1/pending/reject-all', {
      method: 'POST',
    }),

  // Scenario Catalog
  getCatalog: () =>
    request<{ count: number; scenarios: CatalogScenario[] }>('/v1/catalog'),
  queueCatalog: (name: string, force = false, override?: { target?: string; namespace?: string }) =>
    request<{ queued: boolean; name: string; target?: string; namespace?: string; replaced_unapproved_count: number }>(
      `/v1/catalog/${encodeURIComponent(name)}/queue?force=${force}`,
      {
        method: 'POST',
        body: override ? JSON.stringify(override) : undefined,
      }
    ),

  // Experiments & Control
  getHistory: () =>
    request<{ count: number; runs: ExperimentRun[] }>('/v1/experiments/history'),
  runDirect: (experiment: unknown) =>
    request<{ status: string; run_id?: string; result: unknown }>('/v1/experiments/run', {
      method: 'POST',
      body: JSON.stringify(experiment),
    }),
  halt: () =>
    request<{ ctk_aborted?: boolean; state?: string }>('/v1/halt', {
      method: 'POST',
    }),
  sweepOrphans: (olderThanSeconds = 3600) =>
    request<{ swept: boolean; removed_count: number; removed: unknown[] }>(
      `/v1/control/orphan-sweep?older_than_seconds=${olderThanSeconds}`,
      { method: 'POST' }
    ),

  // Audit & Verdict
  getAuditRecent: (limit = 50) =>
    request<{ count: number; events: AuditEvent[] }>(`/v1/audit/recent?limit=${limit}`),
  getAuditSummary: () =>
    request<AuditSummary>('/v1/audit/summary'),
  getLastVerdict: () =>
    request<Record<string, unknown>>('/v1/verdict/last'),

  // Telemetry
  getTelemetryReady: () =>
    request<{ prometheus_configured: boolean; loki_configured: boolean; ready: boolean }>(
      '/v1/telemetry/ready'
    ),
  checkTelemetry: (urls?: { prom_url?: string; loki_url?: string }) =>
    request<TelemetryCheckResponse>('/v1/telemetry/check', {
      method: 'POST',
      body: urls ? JSON.stringify(urls) : undefined,
    }),
  analyzeTelemetry: (payload: {
    lookback_hours: number;
    force?: boolean;
    source?: 'live' | 'export';
    llm_provider?: string;
  }) => {
    const { force = false, ...body } = payload;
    return request<{
      analyzed: boolean;
      metric_series: number;
      log_streams: number;
      anomalies_found: number;
      generated_experiments_count: number;
      replaced_unapproved_count: number;
    }>(`/v1/telemetry/analyze?force=${force}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  // Settings
  getSettings: () =>
    request<{ settings: Record<string, unknown> }>('/v1/settings'),
  updateSettings: (settings: Record<string, unknown>) =>
    request<{ saved: boolean; settings: Record<string, unknown> }>('/v1/settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    }),
};
