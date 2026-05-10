/**
 * Workspace surface types — mirror the Python pydantic shapes in
 * api/workspace.py and the resume edit assistant in
 * agents/resume_edit_assistant.py.
 *
 * Keep these in lockstep with the backend. A field added on the server
 * lands here in the same PR.
 *
 * Re-exports `ReferralPath` from network types because the workspace
 * Network tab is a thin wrapper over the existing path-finder UI.
 */
import type { ReferralPath } from './network'

// Re-export so callers can `import { ReferralPath } from '@/lib/types/workspace'`
// without crossing namespaces in a refactor.
export type { ReferralPath }

export type WorkspaceTabKey = 'role' | 'resume' | 'interview' | 'network' | 'apply'

export interface WorkspaceJob {
  id: number
  title: string
  company: string
  company_id?: string | null
  location?: string | null
  url?: string | null
  description?: string | null
  match_score?: number | null
  fit_details?: WorkspaceFitDetails
  status?: string | null
  discovered_at?: string | null
  applied_at?: string | null
  resume_generated_at?: string | null
  validation_status?: string | null
  archetype?: string | null
}

export interface WorkspaceFitDetails {
  matched_skills?: string[]
  gaps?: string[]
  tailoring_notes?: string
  why_you_fit?: string[]
  // The G2/G1 pipeline writes additional jsonb keys; this index allows
  // us to render unknown structured detail without a schema change.
  [key: string]: unknown
}

export interface WorkspaceApplication {
  id: string
  job_id?: number | null
  status: string
  company?: string | null
  role?: string | null
  applied_date?: string | null
  follow_up_due?: string | null
  notes?: string | null
  cover_email?: string | null
  pdf_url?: string | null
  report_url?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ResumeArtifact {
  build_id: string
  status: string
  iterations?: number | null
  resume_md?: string | null
  user_edited_md?: string | null
  user_edited_at?: string | null
  resume_pdf_url?: string | null
  resume_docx_url?: string | null
  cost_usd_total?: number | null
  latency_ms_total?: number | null
  company_name?: string | null
  persona_version?: number | null
  created_at?: string | null
  finalized_at?: string | null
  error?: string | null
}

export interface WorkspacePersona {
  company_id?: string | null
  company_name?: string | null
  persona_version?: number | null
  ats_keyword_bank?: AtsKeywordBank
  success_patterns?: unknown[]
  failure_patterns?: unknown[]
  metadata?: Record<string, unknown>
  last_synthesized_at?: string | null
}

export interface AtsKeywordBank {
  required?: string[]
  boost?: string[]
  banned?: string[]
  raw_signals?: string
  [key: string]: unknown
}

export interface WorkspaceInterviewPrep {
  has_pack: boolean
  prep_pack_url?: string | null
  status?: string | null
  round_type?: string | null
  round_number?: number | null
  interview_prep_id?: string | null
}

export interface Workspace {
  job: WorkspaceJob
  application: WorkspaceApplication | null
  resume: ResumeArtifact | null
  persona: WorkspacePersona | null
  interview_prep: WorkspaceInterviewPrep | null
  target_company_id: string | null
  warm_intro_paths: ReferralPath[]
  warm_intros_available: boolean
  network_size: number
  generated_at: string
}

// ── Resume edit chat ────────────────────────────────────────────────────
export type EditChatRole = 'user' | 'assistant'

export interface EditChatMessage {
  /** Stable client-side id so React keys are reliable. */
  id: string
  role: EditChatRole
  content: string
  /** Cost of the assistant turn (only present on assistant messages). */
  cost_usd?: number
  /** List of short bullets describing what the LLM changed. */
  fixes_applied?: string[]
  /** ISO timestamp the message was created on the client. */
  created_at: string
}

export interface EditResumeRequest {
  instruction: string
  current_md: string
  chat_history?: { role: EditChatRole; content: string }[]
  mode?: ResumeEditMode
}

export type ResumeEditMode = 'quick_tweak' | 'rebuild_section' | 'full_rebuild'

export interface EditResumeResponse {
  updated_md: string
  response: string
  fixes_applied: string[]
  cost_usd: number
  latency_ms: number
  model: string
  provider: string
}

// ── Build resume (G2) ──────────────────────────────────────────────────
export interface BuildResumeResponse {
  run_id: string
  status: string
  kind: string
  job_id: number
  force: boolean
  max_cost_usd: number | null
  poll_url: string
}

// ── Rebuild section (synchronous mini-graph) ───────────────────────────
export interface RebuildSectionRequest {
  section: string
  edit_intent: string
  current_md: string
}

export interface RebuildSectionResponse {
  /** Full resume after the section was rebuilt and re-spliced. */
  updated_md: string
  /** Chat-style 1-3 sentence explanation. */
  response: string
  fixes_applied: string[]
  cost_usd: number
  latency_ms: number
  model: string
  provider: string
  /** Echo of the section that was rebuilt. */
  section: string
}

// ── Full rebuild (queue G2 force=true) ─────────────────────────────────
export interface FullRebuildRequest {
  edit_intent?: string
  max_cost_usd?: number
}

export interface FullRebuildResponse {
  run_id: string
  status: string
  kind: string
  job_id: number
  force: boolean
  rebuild_scope: 'full'
  edit_intent: string | null
  max_cost_usd: number | null
  poll_url: string
}

// ── Save edit ──────────────────────────────────────────────────────────
export interface SaveResumeEditResponse {
  saved: boolean
  build_id: string
  job_id: number
  company?: string | null
  user_edited_at?: string | null
  byte_size: number
}

// ── Mark applied ───────────────────────────────────────────────────────
export interface MarkAppliedResponse {
  created: boolean
  updated: boolean
  application: WorkspaceApplication
}
