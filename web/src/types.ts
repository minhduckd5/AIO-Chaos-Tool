export type FsmState =
  | 'idle'
  | 'pending_approval'
  | 'steady_state_check'
  | 'injecting'
  | 'verifying'
  | 'rollback'
  | 'unknown';

export type Outcome = 'PASS' | 'FAIL' | 'PARTIAL' | 'NO_TARGET' | 'INCONCLUSIVE';

export interface PendingExperiment {
  name: string;
  target: string;
  namespace?: string;
  fault_types: string[];
  origin: 'ai_advisor' | 'catalog';
  confidence?: number | null;
  hypothesis?: string | null;
  sci_score?: number | null;
  consumed: boolean;
}

export interface PendingResponse {
  state: FsmState;
  count: number;
  pending: PendingExperiment[];
}

export interface CatalogScenario {
  name: string;
  description: string;
  architecture: string;
  fault_type: string;
  tags: string[];
  source: string;
  acceptance_criteria?: Record<string, unknown> | null;
}

export interface ExperimentRun {
  run_id: string;
  name: string;
  actor: string;
  path_used: string;
  started_at: string;
  finished_at?: string | null;
  outcome?: Outcome | string | null;
  status: 'RUNNING' | 'COMPLETED' | 'HALTED';
  duration_seconds?: number | null;
  notes?: string | null;
}

export interface AuditEvent {
  event_id: string;
  schema_version: number;
  timestamp: string;
  actor: string;
  event_type: string;
  path_used: string;
  run_id?: string | null;
  experiment_name?: string | null;
  outcome?: string | null;
  notes?: string | null;
  blast_radius_ref?: {
    max_affected_nodes?: number;
    blocked_namespaces?: string[];
    blocked_services?: string[];
    validation_ok?: boolean;
  } | null;
  criteria_ref?: {
    kind?: string;
    path_or_id?: string;
    summary?: string;
  } | null;
  target_cluster_context?: {
    cluster_type?: string;
    node_count?: number;
    namespace?: string;
  } | null;
}

export interface AuditSummary {
  all: number;
  total: number;
  pass: number;
  fail: number;
  partial: number;
  no_target: number;
  inconclusive: number;
}

export interface TelemetryCheckResponse {
  prometheus: {
    url?: string;
    connected: boolean;
    message: string;
  };
  loki: {
    url?: string;
    connected: boolean;
    message: string;
  };
}

export interface StatusResponse {
  state: FsmState;
  active_experiment?: string | null;
  current_phase?: string | null;
}
