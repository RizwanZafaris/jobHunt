/**
 * Phase 3 — Interview Studio types.
 *
 * Mirrors the Pydantic shapes in api/interview_studio.py. When you add a
 * field there, add it here too.
 */

export type ConceptLevel = 'basics' | 'intermediate' | 'advanced'

export type PrepSection =
  | 'likely_questions'
  | 'star_stories'
  | 'company_hooks'
  | 'red_flag_questions'
  | 'salary_negotiation'

export type TutorRole = 'user' | 'tutor' | 'system'

export type OutcomeRoundType =
  | 'recruiter'
  | 'hm'
  | 'panel'
  | 'exec'
  | 'technical'
  | 'take_home'

// ─── Prep-pack item shapes (loose JSONB on the server) ─────────────────────
export interface LikelyQuestion {
  question: string
  competency?: string
  importance?: string
  source?: string
  text?: string
}

export interface StarStoryRef {
  question?: string
  story_id?: string
  title?: string
  match_quality?: string
  needs_rizwan_input?: boolean
}

export type CompanyHook = string | { hook: string }
export type RedFlagQuestion = string | { question: string }

// ─── The InterviewPrep block ──────────────────────────────────────────────
export interface InterviewPrep {
  id: string | null
  has_pack: boolean
  status: string | null
  round_number: number | null
  round_type: string | null
  prep_pack_md: string | null
  prep_pack_url: string | null
  likely_questions: LikelyQuestion[]
  star_stories: StarStoryRef[]
  company_hooks: CompanyHook[]
  red_flag_questions: RedFlagQuestion[]
  salary_negotiation_notes: string | null
  company_name: string | null
  created_at: string | null
  finalized_at: string | null
}

// ─── Tutor messages ───────────────────────────────────────────────────────
export interface TutorMessage {
  id: string
  role: TutorRole
  content: string
  concept_level: ConceptLevel | null
  cited_section: PrepSection | null
  suggested_next: string | null
  created_at: string
}

// ─── Application + job (subset of Phase 1 shape) ──────────────────────────
export interface StudioApplication {
  id: string
  company: string | null
  role: string | null
  score: number | null
  status: string | null
  applied_date: string | null
  job_id: number | null
  notes: string | null
}

export interface StudioJob {
  id: number | null
  title: string | null
  company: string | null
  location: string | null
  url: string | null
  match_score: number | null
  description: string | null
}

// ─── Round outcomes (already-logged) ──────────────────────────────────────
export interface InterviewRoundOutcome {
  id: string
  application_id: string
  round_number: number
  round_type: string | null
  passed: boolean | null
  questions_asked: string[] | null
  feedback_text: string | null
  interviewer_names: string[] | null
  conducted_at: string | null
  logged_at: string
}

// ─── Top-level studio payload ─────────────────────────────────────────────
export interface InterviewStudio {
  application: StudioApplication
  job: StudioJob
  interview_prep: InterviewPrep
  outcomes_logged: InterviewRoundOutcome[]
  tutor_chat_history: TutorMessage[]
  current_concept_level: ConceptLevel
  tutor_max_cost_usd: number
  generated_at: string
}

// ─── Request / response shapes ────────────────────────────────────────────
export interface TutorChatRequest {
  message: string
  concept_level?: ConceptLevel
}

export interface TutorChatResponse {
  response: string
  suggested_next_question: string | null
  concept_level_recommended: ConceptLevel
  cited_section: PrepSection | null
  cost_usd: number
  cost_capped: boolean
  user_message_id: string | null
  tutor_message_id: string | null
}

export interface OutcomeLogInput {
  round_number: number
  round_type: OutcomeRoundType | string
  passed: boolean | null
  questions_asked: string[]
  feedback_text: string | null
  interviewer_names?: string[]
  conducted_at?: string | null
}

export interface LogOutcomeResponse {
  outcome_id: string
  credit_summary: {
    outcome_event_id?: string
    kind?: string
    credited_knowledge_count?: number
    delta_per_row?: number
    source?: string | null
    error?: string | null
  }
  refines_persona_on_next_cron: boolean
}

export interface BuildPrepPackResponse {
  job_run_id: string
  enqueued: boolean
}

// ─── Concept-ladder labelling (UI-only) ───────────────────────────────────
export const CONCEPT_LEVELS: ConceptLevel[] = ['basics', 'intermediate', 'advanced']

export const CONCEPT_LEVEL_LABEL: Record<ConceptLevel, string> = {
  basics: 'Basics',
  intermediate: 'Intermediate',
  advanced: 'Advanced',
}

export const CONCEPT_LEVEL_DESC: Record<ConceptLevel, string> = {
  basics: 'Plain language, jargon defined, one worked example end-to-end.',
  intermediate: 'Vocabulary assumed. Compare approaches. Second-order tradeoffs.',
  advanced: 'Sparring. Push-back. Contrarian takes.',
}

export const PREP_SECTION_LABEL: Record<PrepSection, string> = {
  likely_questions: 'Likely Questions',
  star_stories: 'STAR Stories',
  company_hooks: 'Company Hooks',
  red_flag_questions: 'Red-Flag Questions',
  salary_negotiation: 'Salary Negotiation',
}

export const ROUND_TYPE_LABEL: Record<string, string> = {
  recruiter: 'Recruiter',
  hm: 'Hiring Manager',
  panel: 'Panel',
  exec: 'Executive',
  technical: 'Technical',
  take_home: 'Take-Home',
}
