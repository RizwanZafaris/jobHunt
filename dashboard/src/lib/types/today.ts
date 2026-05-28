/**
 * /today action types.
 *
 * The /today surface answers a single question: "what should I do right now?"
 * Each TodayAction is a ranked, actionable card that maps to one concrete
 * next step in the job-hunt loop.
 *
 * Kinds correspond 1:1 to ranking bands the future /actions/today endpoint
 * will emit. See: docs/TODAY_RANKING.md (TODO).
 */

export type TodayActionKind =
  | 'resume_ready'           // resume URL available, not applied
  | 'score_high_no_resume'   // score >= 85, no resume yet — kick off G2
  | 'score_below_threshold'  // job blocked at <85 — adjust or skip
  | 'stale_application'      // applied 7+ days ago, no outcome logged
  | 'persona_stale'          // persona last refreshed >14 days, kick off /personas/refresh-news
  | 'linkedin_post_due'      // P1 placeholder
  | 'follow_up_overdue'      // G6 overdue follow-up
  | 'follow_up_urgent'       // G6 urgent follow-up

export type TodayActionState = 'ready' | 'blocked' | 'stale' | 'pending'

export type TodayActionPrimaryHandler = 'copy' | 'kickoff_g2' | 'log_outcome'

export interface TodayActionPrimary {
  label: string
  href?: string
  onClick?: TodayActionPrimaryHandler
}

export interface TodayActionSecondary {
  label: string
  href?: string
}

export type LetterGrade = 'A' | 'B' | 'C' | 'D' | 'F'

export interface TodayActionMeta {
  score?: number
  company?: string
  date?: string
  // Phase 2 §4.1 (G5) — A/B/C/D/F letter grade from
  // jobs.fit_score_breakdown.composite. Optional because pre-G5
  // discovery rows + linkedin_post_due / stale_application etc.
  // cards have no grade. The /today A-F chip group consumes this.
  letterGrade?: LetterGrade
  // 2026-05-26 stale-card fix — required for the dismiss button +
  // for explaining age/surface penalties in the UI tooltip.
  jobId?: number
  createdAt?: string         // ISO-8601, used for "shown for N days" pill
  surfaceCount?: number      // n times this card was surfaced
}

export interface TodayAction {
  id: string
  kind: TodayActionKind
  title: string
  subtitle?: string
  state: TodayActionState
  primary: TodayActionPrimary
  secondary?: TodayActionSecondary
  meta?: TodayActionMeta
}

// 2026-05-26 — sectioned /today response shape.
// Returned by GET /actions/today/sections (api/actions.py::get_today_sections).
export type TodaySectionTone =
  | 'danger'    // overdue follow-ups
  | 'warning'   // urgent follow-ups
  | 'info'      // LinkedIn post due
  | 'success'   // ready-to-apply, hot leads
  | 'neutral'   // stale apps, stale personas, below threshold

export interface TodaySection {
  kind: TodayActionKind
  label: string                  // "Hot leads" / "Stale applications"
  subtitle: string               // one-line purpose / urgency hint
  icon: string                   // IconName matching '@/components/ui/Icon'
  tone: TodaySectionTone
  cards: TodayAction[]
  count_total: number            // may exceed cards.length when has_more
  count_visible: number          // cards.length
  has_more: boolean
  empty_state: string            // shown when cards is empty
}

export interface TodaySectionsResponse {
  sections: TodaySection[]
  total: number
  generated_at: string
}

// ─── /today/recommended (AI-curated apply-now list, 2026-05-27) ─────────
export type RecommendationTier = 'apply_now' | 'strong' | 'stretch'

export interface RecommendedJob {
  id: number
  company: string
  title: string
  location: string
  url: string
  source: string
  archetype: string
  match_score: number | null
  recommendation_score: number | null
  recommendation_reasoning: string
  recommended_at: string | null
  discovered_at_days_old: number | null
}

export interface TodayRecommendedResponse {
  ok: boolean
  total: number
  by_tier: Record<RecommendationTier, RecommendedJob[]>
  generated_at: string
}
