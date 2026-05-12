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
