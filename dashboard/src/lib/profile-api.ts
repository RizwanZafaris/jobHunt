// Server-side profile fetches. Use in React Server Components only.
// Client components should call /api/proxy/profile/...

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

const isBrowser = typeof window !== 'undefined'
const baseUrl = isBrowser ? '/api/proxy' : API_URL
const headers: Record<string, string> = isBrowser
  ? { 'Content-Type': 'application/json' }
  : { 'Content-Type': 'application/json', 'X-Secret-Key': SECRET_KEY }

export interface ProfileMaster {
  id: number
  name: string
  headline: string
  summary: string
  location: string
  email: string
  phones: string[]
  linkedin_url: string
  core_competencies: string[]
  technical_knowledge: string[]
  languages: { name: string; level: string }[]
  ai_solutions: { title: string; description: string }[]
  tailored_resumes_count: number
}

export interface ProfileExperienceGroup {
  label: string
  bullets: string[]
}

export interface ProfileExperience {
  id: number
  sort_order: number
  title: string
  company: string
  location: string
  scope: string
  dates: string
  summary: string
  highlights: string[]
  groups: ProfileExperienceGroup[]
}

export interface ProfileCertification {
  id: number
  name: string
  full_name: string
  sort_order: number
}

export interface ProfileEducation {
  id: number
  title: string
  details: string
  year: string
  notes: string
  sort_order: number
}

export interface ProfileResponse {
  master: ProfileMaster | null
  experience: ProfileExperience[]
  certifications: ProfileCertification[]
  education: ProfileEducation[]
}

export interface KeywordRow {
  id: number
  keyword: string
  category: string
  total_occurrences: number
  files_count: number
  coverage_pct: number
  avg_per_file: number
  ats_strength: number
}

export interface KeywordCategory {
  category: string
  keyword_count: number
  total_occurrences: number
  avg_strength: number
  top_keywords: string[]
}

export interface KeywordsResponse {
  keywords: KeywordRow[]
  categories: KeywordCategory[]
}

export interface SourceDocument {
  id: number
  file_hash: string
  file_name: string
  document_class: string
  char_count: number
  file_size: number
  parsed_at: string
}

export interface SourcesResponse {
  documents: SourceDocument[]
  total: number
  by_class: Record<string, number>
}

export async function fetchProfile(): Promise<ProfileResponse> {
  const res = await fetch(`${baseUrl}/profile`, { headers, next: { revalidate: 300 } })
  if (!res.ok) throw new Error(`Failed to fetch profile: ${res.status}`)
  return res.json()
}

export async function fetchKeywords(category?: string): Promise<KeywordsResponse> {
  const q = category ? `?category=${encodeURIComponent(category)}` : ''
  const res = await fetch(`${baseUrl}/profile/keywords${q}`, { headers, next: { revalidate: 300 } })
  if (!res.ok) throw new Error(`Failed to fetch keywords: ${res.status}`)
  return res.json()
}

export async function fetchSources(): Promise<SourcesResponse> {
  const res = await fetch(`${baseUrl}/profile/sources`, { headers, next: { revalidate: 300 } })
  if (!res.ok) throw new Error(`Failed to fetch sources: ${res.status}`)
  return res.json()
}

// ── Phase B: edit ──────────────────────────────────────────────────────

export async function updateProfile(updates: Partial<ProfileMaster>) {
  const res = await fetch(`${baseUrl}/profile`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error(`Failed to update profile: ${res.status}`)
  return res.json()
}

export async function updateExperience(id: number, updates: Partial<ProfileExperience>) {
  const res = await fetch(`${baseUrl}/profile/experience/${id}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error(`Failed to update experience: ${res.status}`)
  return res.json()
}

// ── Phase C: recommendations ──────────────────────────────────────────

export interface Recommendation {
  id: number
  kind: 'missing_keyword' | 'weak_category' | 'conflict' | 'quantify'
  severity: 'low' | 'medium' | 'high'
  title: string
  detail: string
  related_keyword?: string
  related_category?: string
  dismissed: boolean
  created_at: string
}

export interface RecommendationsResponse {
  recommendations: Recommendation[]
  total: number
  by_kind: Record<string, number>
  by_severity: Record<string, number>
}

export async function fetchRecommendations(): Promise<RecommendationsResponse> {
  const res = await fetch(`${baseUrl}/profile/recommendations`, { headers, next: { revalidate: 60 } })
  if (!res.ok) throw new Error(`Failed to fetch recommendations: ${res.status}`)
  return res.json()
}

export async function dismissRecommendation(id: number, dismissed = true) {
  const res = await fetch(`${baseUrl}/profile/recommendations/${id}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ dismissed }),
  })
  if (!res.ok) throw new Error(`Failed to dismiss recommendation: ${res.status}`)
  return res.json()
}

export async function regenerateRecommendations() {
  const res = await fetch(`${baseUrl}/profile/recommendations/regenerate`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`Failed to regenerate: ${res.status}`)
  return res.json()
}

// ── Phase D: target companies ──────────────────────────────────────────

export interface TargetCompany {
  id: string
  name: string
  category: string
  priority: 'high' | 'medium' | 'low'
  is_target: boolean
  careers_url: string
  notes: string | null
  target_added_at: string
  last_scanned_at: string | null
}

export interface TargetCompaniesResponse {
  companies: TargetCompany[]
  total: number
  by_category: Record<string, TargetCompany[]>
}

export async function fetchTargetCompanies(): Promise<TargetCompaniesResponse> {
  const res = await fetch(`${baseUrl}/companies/targets`, { headers, cache: 'no-store' })
  if (!res.ok) throw new Error(`Failed to fetch target companies: ${res.status}`)
  return res.json()
}

export async function addTargetCompany(payload: {
  name: string
  category?: string
  priority?: string
  careers_url?: string
  notes?: string
}) {
  const res = await fetch(`${baseUrl}/companies/targets`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Failed to add company: ${res.status}`)
  return res.json()
}

export async function updateCompany(id: string, updates: Partial<TargetCompany>) {
  const res = await fetch(`${baseUrl}/companies/${id}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error(`Failed to update company: ${res.status}`)
  return res.json()
}

export async function removeTargetCompany(id: string) {
  const res = await fetch(`${baseUrl}/companies/${id}`, {
    method: 'DELETE',
    headers,
  })
  if (!res.ok) throw new Error(`Failed to remove: ${res.status}`)
  return res.json()
}

export async function runTargetsPipeline() {
  const res = await fetch(`${baseUrl}/pipeline/run-targets`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`Failed to start pipeline: ${res.status}`)
  return res.json()
}

// ── Phase D: jobs detail ───────────────────────────────────────────────

export interface JobDetail {
  job: {
    id: number
    title: string
    company: string
    location: string
    description: string
    url: string
    source: string
    match_score: number
    fit_details: any
    status: string
    resume_path: string | null
    email_path: string | null
    interview_path: string | null
    discovered_at: string
    applied_at: string | null
    // Workflow v2
    archetype: string | null
    legitimacy_tier: string | null
    legitimacy_signals: string[] | null
    resume_generated_at: string | null
    evaluation_blocks: any
  }
  artifacts: Record<string, { exists: boolean; path?: string; size?: number; content?: string }>
  application: {
    id: string
    status: string
    notes: string | null
    applied_date: string | null
    follow_up_due: string | null
  } | null
}

export async function generateResumeForJob(jobId: number) {
  const res = await fetch(`${baseUrl}/jobs/${jobId}/generate-resume`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data?.detail || `Failed: ${res.status}`)
  }
  return res.json()
}

export async function reclassifyJobs(onlyMissing = true) {
  const url = `${baseUrl}/jobs/reclassify?only_missing=${onlyMissing}`
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`Failed: ${res.status}`)
  return res.json()
}

export async function fetchJobDetail(jobId: number | string): Promise<JobDetail> {
  const res = await fetch(`${baseUrl}/jobs/${jobId}/detail`, { headers, cache: 'no-store' })
  if (!res.ok) throw new Error(`Failed to fetch job detail: ${res.status}`)
  return res.json()
}

// ── Phase D: applications ──────────────────────────────────────────────

export interface Application {
  id: string
  job_id: number
  company: string
  role: string
  status: string
  applied_date: string | null
  follow_up_due: string | null
  notes: string | null
  resume_path: string | null
  email_path: string | null
  interview_path: string | null
  score: number
  created_at: string
  job?: {
    id: number
    title: string
    company: string
    location: string
    match_score: number
    url: string
  }
}

export interface ApplicationsResponse {
  applications: Application[]
  by_status: Record<string, Application[]>
  total: number
}

export async function fetchApplications(): Promise<ApplicationsResponse> {
  const res = await fetch(`${baseUrl}/applications`, { headers, cache: 'no-store' })
  if (!res.ok) throw new Error(`Failed to fetch applications: ${res.status}`)
  return res.json()
}

export async function createApplication(jobId: number, status = 'evaluated', notes?: string) {
  const res = await fetch(`${baseUrl}/applications`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ job_id: jobId, status, notes }),
  })
  if (!res.ok) throw new Error(`Failed to create application: ${res.status}`)
  return res.json()
}

export async function updateApplication(id: string, updates: Partial<Application>) {
  const res = await fetch(`${baseUrl}/applications/${id}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(updates),
  })
  if (!res.ok) throw new Error(`Failed to update application: ${res.status}`)
  return res.json()
}

// ── Resume Outcomes (Phase 1.5 — the learning loop) ───────────────────

export interface ResumeOutcome {
  id: string
  job_id: number | null
  resume_build_id: string | null
  application_id: string | null
  company_name: string | null
  ats_passed: boolean | null
  submitted_at: string | null
  recruiter_responded: boolean | null
  recruiter_response_at: string | null
  interview_received: boolean | null
  rounds_reached: number | null
  offer_received: boolean | null
  rejected_reason: string | null
  self_rated_quality: number | null   // 1-5
  notes: string | null
  logged_at: string
  updated_at: string
}

export interface OutcomeUpsertPayload {
  job_id?: number
  resume_build_id?: string
  application_id?: string
  company_name?: string
  ats_passed?: boolean | null
  submitted_at?: string | null
  recruiter_responded?: boolean | null
  recruiter_response_at?: string | null
  interview_received?: boolean | null
  rounds_reached?: number | null
  offer_received?: boolean | null
  rejected_reason?: string | null
  self_rated_quality?: number | null
  notes?: string | null
}

export async function fetchOutcomeByJob(
  jobId: number,
): Promise<{ outcome: ResumeOutcome | null }> {
  const res = await fetch(`${baseUrl}/resumes/outcomes/by-job/${jobId}`, {
    headers,
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`Failed to fetch outcome: ${res.status}`)
  return res.json()
}

export async function saveOutcome(payload: OutcomeUpsertPayload) {
  const res = await fetch(`${baseUrl}/resumes/outcomes`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data?.detail || `Failed to save outcome: ${res.status}`)
  }
  return res.json()
}

export interface ConversionFunnelRow {
  company_name: string
  resumes_built: number
  responses: number | null
  interviews: number | null
  offers: number | null
  avg_polisher_score: number | null
  avg_cost_usd: number | null
}

export async function fetchConversionFunnel(): Promise<{
  funnel: ConversionFunnelRow[]
  warning?: string
}> {
  const res = await fetch(`${baseUrl}/resumes/outcomes/conversion`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch conversion funnel: ${res.status}`)
  return res.json()
}

// ── Company Personas (Phase 1.6) ──────────────────────────────────────

export interface PersonaRow {
  company_name: string
  persona_version: number
  n_examples_used: number
  last_synthesized_at: string
  metadata: {
    persona_quality?: 'high' | 'medium' | 'low'
    unknown_sections?: number
    seeded_from?: string
    seeded_at?: string
  }
  ats_keyword_bank: {
    required?: string[]
    boost?: string[]
    banned?: string[]
    raw_signals?: string
  }
}

export interface PersonasResponse {
  personas: PersonaRow[]
  total: number
  by_quality: Record<string, number>
}

export async function fetchPersonas(): Promise<PersonasResponse> {
  const res = await fetch(`${baseUrl}/personas`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch personas: ${res.status}`)
  return res.json()
}

export async function fetchPersonaDetail(companyName: string) {
  const res = await fetch(`${baseUrl}/personas/${encodeURIComponent(companyName)}`, {
    headers,
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`Failed to fetch persona: ${res.status}`)
  return res.json()
}

export async function triggerPersonaSynthesis(opts: {
  company_name?: string
  force?: boolean
}) {
  const params = new URLSearchParams()
  if (opts.company_name) params.set('company_name', opts.company_name)
  if (opts.force) params.set('force', 'true')
  const res = await fetch(`${baseUrl}/personas/synthesize?${params}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`Failed to trigger synthesis: ${res.status}`)
  return res.json()
}

// ── Cost Observability (Phase 1.8) ─────────────────────────────────────

export interface CostBucket {
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  avg_latency_ms: number
}

export interface CostSummary {
  today: CostBucket
  last_7d: CostBucket
  last_30d: CostBucket
  avg_per_resume_build: number
  n_resume_builds: number
  warning?: string
}

export interface DailyCostRow {
  day: string                 // 'YYYY-MM-DD'
  provider: string
  model: string
  calls: number
  in_toks: number
  out_toks: number
  total_cost_usd: number
  avg_latency_ms: number
}

export interface DailyCostResponse {
  days: number
  rows: DailyCostRow[]
  warning?: string
}

export interface CostByProviderRow {
  provider: string
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  avg_latency_ms: number
}

export interface CostByAgentRow {
  agent_name: string
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  avg_latency_ms: number
  providers: string[]
  models: string[]
}

export interface CostByResumeBuildRow {
  resume_build_id: string
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  company_name?: string | null
  polisher_score?: number | null
  status?: string | null
  iterations?: number | null
  created_at?: string | null
}

export interface AgentCallLogRow {
  id: number
  called_at: string
  agent_name: string | null
  graph: string | null
  node_name: string | null
  provider: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  latency_ms: number
  job_id: number | null
  resume_build_id: string | null
  error: string | null
}

export async function fetchCostSummary(): Promise<CostSummary> {
  const res = await fetch(`${baseUrl}/costs/summary`, { headers, cache: 'no-store' })
  if (!res.ok) throw new Error(`Failed to fetch cost summary: ${res.status}`)
  return res.json()
}

export async function fetchDailyCost(days = 30): Promise<DailyCostResponse> {
  const res = await fetch(`${baseUrl}/costs/daily?days=${days}`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch daily cost: ${res.status}`)
  return res.json()
}

export async function fetchCostByProvider(days = 7): Promise<{
  days: number
  providers: CostByProviderRow[]
}> {
  const res = await fetch(`${baseUrl}/costs/by-provider?days=${days}`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch provider costs: ${res.status}`)
  return res.json()
}

export async function fetchCostByAgent(days = 7): Promise<{
  days: number
  agents: CostByAgentRow[]
}> {
  const res = await fetch(`${baseUrl}/costs/by-agent?days=${days}`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch agent costs: ${res.status}`)
  return res.json()
}

export async function fetchCostByResumeBuild(limit = 20): Promise<{
  builds: CostByResumeBuildRow[]
}> {
  const res = await fetch(`${baseUrl}/costs/by-resume-build?limit=${limit}`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch by-build costs: ${res.status}`)
  return res.json()
}

export async function fetchRecentCalls(opts?: {
  limit?: number
  provider?: string
  agent_name?: string
  has_error?: boolean
}): Promise<{ calls: AgentCallLogRow[]; warning?: string }> {
  const params = new URLSearchParams()
  if (opts?.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts?.provider) params.set('provider', opts.provider)
  if (opts?.agent_name) params.set('agent_name', opts.agent_name)
  if (opts?.has_error !== undefined) params.set('has_error', String(opts.has_error))
  const res = await fetch(`${baseUrl}/costs/recent-calls?${params}`, {
    headers,
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`Failed to fetch recent calls: ${res.status}`)
  return res.json()
}

// ── Phase 1.9: health + perf observability ─────────────────────────────

export interface ProviderHealthRow {
  provider: string
  calls_7d: number
  errors_7d: number
  error_rate_pct: number
  p50_latency_ms: number
  p95_latency_ms: number
  p99_latency_ms: number
  total_cost_usd_7d: number
  last_call_at: string | null
}

export interface AgentCallLogStats {
  total_rows: number
  rows_last_24h?: number
  rows_last_7d?: number
  oldest_row_at?: string | null
  newest_row_at?: string | null
  total_size?: string
  indexes_size?: string
  warning?: string
}

export async function fetchCostHealth(): Promise<{
  providers: ProviderHealthRow[]
  warning?: string
}> {
  const res = await fetch(`${baseUrl}/costs/health`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) throw new Error(`Failed to fetch cost health: ${res.status}`)
  return res.json()
}

export async function fetchLogStats(): Promise<AgentCallLogStats> {
  const res = await fetch(`${baseUrl}/costs/log-stats`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`Failed to fetch log stats: ${res.status}`)
  return res.json()
}

export async function cleanupCallLog(daysToKeep: number = 365): Promise<{
  deleted: number
  days_to_keep: number
}> {
  const res = await fetch(`${baseUrl}/costs/cleanup`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ days_to_keep: daysToKeep }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data?.detail || `Cleanup failed: ${res.status}`)
  }
  return res.json()
}

// ── Company knowledge / research ───────────────────────────────────────

export interface CompanyKnowledgeSection {
  section: string
  content: string
  source_url: string | null
  scraped_at: string
}

export interface CompanyKnowledgeResponse {
  company: TargetCompany | null
  knowledge: CompanyKnowledgeSection[]
  section_count: number
}

export async function fetchCompanyKnowledge(name: string): Promise<CompanyKnowledgeResponse> {
  const res = await fetch(`${baseUrl}/companies/${encodeURIComponent(name)}/knowledge`, {
    headers,
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`Failed to fetch company knowledge: ${res.status}`)
  return res.json()
}

export async function triggerCompanyResearch(opts: {
  company_name?: string
  priority?: string
  force?: boolean
}) {
  const res = await fetch(`${baseUrl}/companies/research`, {
    method: 'POST',
    headers,
    body: JSON.stringify(opts),
  })
  if (!res.ok) throw new Error(`Failed to start research: ${res.status}`)
  return res.json()
}
