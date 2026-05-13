/**
 * G8 Offer Evaluation types.
 *
 * Mirrors `offer_evaluations` table schema (migration 026).
 * Backend: api/offers.py::OfferEvaluationResponse.
 */

export type Recommendation = 'accept' | 'negotiate' | 'decline' | 'consider'

export type UserDecision =
  | 'accepted'
  | 'negotiated_accepted'
  | 'negotiated_declined'
  | 'declined'
  | 'expired'

export interface RiskFactor {
  kind: string
  severity: number
  description: string
  evidence?: string | null
  cite_ids?: string[]
}

export interface CitationRef {
  kind: string
  id: string
  title?: string | null
  snippet?: string | null
  used_by_node?: string
}

export interface MarketCitation {
  url?: string
  title?: string
  kind?: string
  id?: string
}

export interface OfferEvaluation {
  id: string
  user_id: string
  application_id: string
  job_id?: number | null
  // Comp components
  base_salary_usd?: number | null
  target_bonus_usd?: number | null
  equity_grant_usd?: number | null
  sign_on_bonus_usd?: number | null
  other_benefits_usd?: number | null
  total_comp_year_1?: number | null
  total_comp_4yr_avg?: number | null
  currency: string
  // Role descriptors
  level?: string | null
  title?: string | null
  location?: string | null
  start_date?: string | null
  // Market research
  market_p25?: number | null
  market_p50?: number | null
  market_p75?: number | null
  market_p90?: number | null
  market_summary?: string | null
  market_citations: MarketCitation[]
  // Negotiation plan
  negotiation_anchor?: number | null
  negotiation_walkaway?: number | null
  negotiation_script?: string | null
  archetype_strategy?: string | null
  // Risk
  risk_score?: number | null
  risk_factors: RiskFactor[]
  recommendation?: Recommendation | null
  confidence?: number | null
  risk_adjusted_total_comp?: number | null
  sleep_on_it_score?: number | null
  rationale_md?: string | null
  // Telemetry
  total_cost_usd?: number | null
  total_latency_ms?: number | null
  cites: CitationRef[]
  // Outcome capture
  user_decision?: UserDecision | null
  final_total_comp?: number | null
  user_decision_at?: string | null
  // Timestamps
  created_at?: string | null
  updated_at?: string | null
}

export interface EvaluateOfferRequest {
  application_id: string
  offer_text: string
  force?: boolean
}

export interface OfferDecisionRequest {
  user_decision: UserDecision
  final_total_comp?: number
}
