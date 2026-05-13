/**
 * Pattern Analytics types.
 *
 * Mirrors api/analytics.py response models. The six endpoints all hang
 * off /analytics/* and return JSON with a `message` field that the
 * dashboard can show as a "Not enough data yet" placeholder below the
 * sample-size threshold.
 */

export interface ConversionRates {
  applied_to_responded: number
  responded_to_interviewed: number
  interviewed_to_offered: number
  offered_to_accepted: number
  applied_to_offered: number
  applied_to_accepted: number
}

export interface FunnelResponse {
  applied: number
  responded: number
  interviewed: number
  offered: number
  accepted: number
  total: number
  conversion_rates: ConversionRates
  is_sufficient_data: boolean
  message: string
}

export interface PartitionEntry {
  key: string
  applied: number
  responded: number
  interviewed: number
  offered: number
  accepted: number
  total: number
  conversion_rates: ConversionRates
}

export interface PartitionedFunnelResponse {
  dimension: string
  partitions: PartitionEntry[]
  is_sufficient_data: boolean
  message: string
}

export interface RejectionPatternItem {
  dimension: string
  value: string
  n: number
  rate: number
  baseline_rate: number
  lift: number
  avg_days_to_rejection: number | null
}

export interface RejectionPatternsResponse {
  patterns: RejectionPatternItem[]
  is_sufficient_data: boolean
  message: string
}

export interface CostEfficiencyItem {
  company_name: string
  total_cost_usd: number
  total_calls: number
  interviews: number
  offers: number
  cost_per_outcome: number | null
  is_sufficient_data: boolean
}

export interface CostEfficiencyResponse {
  rows: CostEfficiencyItem[]
  message: string
}
