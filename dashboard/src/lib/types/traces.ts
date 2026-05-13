/**
 * LangGraph trace observability types (Stream C, 2026-05-13).
 * Mirrors api/traces.py response models.
 */

export interface GraphRunSummary {
  graph: string
  run_key: string
  started_at: string | null
  last_event_at: string | null
  total_nodes: number
  error_nodes: number
  total_cost_usd: number
  total_latency_ms: number
  nodes_executed: string[]
  status: 'converged' | 'in_progress' | 'failed' | 'unknown'
}

export interface RecentRunsResponse {
  runs: GraphRunSummary[]
  message: string
}

export interface NodeEvent {
  node_name: string | null
  agent_name: string | null
  provider: string | null
  model: string | null
  cost_usd: number
  latency_ms: number
  input_tokens: number
  output_tokens: number
  error: string | null
  called_at: string | null
}

export interface TraceDetailResponse {
  graph: string
  run_key: string
  total_nodes: number
  total_cost_usd: number
  total_latency_ms: number
  status: string
  nodes: NodeEvent[]
}

export interface ErrorEvent {
  graph: string | null
  node_name: string | null
  agent_name: string | null
  model: string | null
  error: string
  called_at: string | null
  run_key: string | null
}

export interface ErrorsResponse {
  errors: ErrorEvent[]
  message: string
}
