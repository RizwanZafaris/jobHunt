/**
 * Network surface types — mirror the API shapes in agents/referral_graph.py
 * (Person, ReferralPath) and api/network.py (TargetCoverageRow,
 * ImportSummary).
 *
 * Keep these in lockstep with the Python pydantic models — when a field
 * is added on the server side, add it here and either expose it in the
 * UI or document why it's deliberately ignored.
 */

export type EdgeKind =
  | 'me_first_degree'
  | 'colleague'
  | 'classmate'
  | 'friend'
  | 'family'
  | 'introduced_by'
  | 'same_company_overlap'
  | 'referenced_in_outreach'

export type PersonSource =
  | 'me'
  | 'manual'
  | 'linkedin_csv'
  | 'google_contacts'
  | 'apify_scrape'
  | 'inferred'
  | 'introduction'

export interface Person {
  id: string
  full_name: string
  linkedin_url?: string | null
  headline?: string | null
  profile_picture_url?: string | null
  current_company?: string | null
  current_role?: string | null
  source?: PersonSource | null
}

export interface Employment {
  id: string
  person_id: string
  company_id?: string | null
  company_name: string
  role?: string | null
  role_seniority?:
    | 'ic'
    | 'senior_ic'
    | 'staff'
    | 'principal'
    | 'manager'
    | 'senior_manager'
    | 'director'
    | 'vp'
    | 'c_level'
    | null
  started_at?: string | null
  ended_at?: string | null
  is_current: boolean
}

export interface ReferralPath {
  target_employee: Person
  target_company_id: string
  target_company_name?: string | null
  hop_count: number
  total_strength: number
  path: Person[]
  edge_kinds: string[]
}

export interface TargetCoverage {
  target_company_id: string
  name?: string | null
  paths_count_1hop: number
  paths_count_2hop: number
  top_path?: ReferralPath | null
}

export interface ImportSummary {
  imported: number
  skipped: number
  edges_created: number
  employments_created: number
  errors: string[]
}

export interface IntroDraft {
  subject: string
  body: string
  length_words: number
  tone_notes: string
  mode?: 'intro_email' | 'target_outreach'
  cost_usd?: number
  model?: string
  provider?: string
  error?: 'draft_failed' | 'cost_capped'
  detail?: string
}
