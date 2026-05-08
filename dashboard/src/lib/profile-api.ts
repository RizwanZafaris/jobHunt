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
