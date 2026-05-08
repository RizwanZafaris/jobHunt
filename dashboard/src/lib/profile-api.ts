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
