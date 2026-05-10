/**
 * LinkedIn presence engine types — mirror of /linkedin/* API surface.
 *
 * The dashboard uses these types in three places:
 *   - dashboard/src/app/linkedin/page.tsx                 — content calendar
 *   - dashboard/src/components/linkedin/DraftCard.tsx     — single card
 *   - dashboard/src/lib/mock/linkedin.ts                  — fixtures
 *
 * Source of truth: db/migrations/2026_05_10_005_linkedin_drafts.sql
 * + the /linkedin/* router in api/linkedin.py.
 */

export const LINKEDIN_ANGLES = [
  'news_commentary',
  'contrarian_take',
  'build_in_public',
  'lesson_learned',
  'industry_analysis',
] as const

export type LinkedInAngle = (typeof LINKEDIN_ANGLES)[number]

export const LINKEDIN_DRAFT_STATUSES = [
  'draft',
  'approved',
  'scheduled',
  'posted',
  'rejected',
  'expired',
] as const

export type LinkedInDraftStatus = (typeof LINKEDIN_DRAFT_STATUSES)[number]

export type CritiqueSeverity = 'P0' | 'P1' | 'P2'

export interface CritiqueIssue {
  severity: CritiqueSeverity
  kind: string
  span?: string
  fix?: string
}

export interface CritiquePayload {
  issues: CritiqueIssue[]
  passes?: string[]
  verdict?: 'ship_after_fix' | 'ship_as_is' | 'rewrite'
}

export interface EngagementMetrics {
  likes?: number
  comments?: number
  views?: number
  recordedAt?: string
  notifiedAt?: string
}

/**
 * Visual creation brief produced by the G4 image_brief node.
 * The engine BRIEFS, it does NOT generate. The user picks the provider
 * and either feeds `prompt` to a text-to-image model OR (preferred when
 * applicable) pulls a screenshot from `referenceUrl`.
 *
 * Source of truth: agents/g4_linkedin_graph.py::IMAGE_BRIEF_SYSTEM
 */
export const IMAGE_BRIEF_KINDS = [
  'reference_news_image',
  'data_viz',
  'screenshot_quote',
  'infographic',
  'diagram',
] as const
export type ImageBriefKind = (typeof IMAGE_BRIEF_KINDS)[number]

export const IMAGE_PROVIDERS = [
  'dall-e-3',
  'imagen-3',
  'midjourney',
  'stock_photo',
  'screenshot_only',
] as const
export type ImageProvider = (typeof IMAGE_PROVIDERS)[number]

export interface ImageBrief {
  kind: ImageBriefKind
  prompt: string                         // text-to-image prompt (under 100 words)
  compositionNotes: string               // aspect ratio + key composition rule
  dataAnchors: string[]                  // numbers/claims/quotes the image must illustrate
  referenceUrl: string | null            // source image / news article URL
  altText: string                        // accessibility-grade one-sentence description
  recommendedProvider: ImageProvider
  aiDisclosureRecommended: boolean       // user should append "Made with X" if true
  rationale: string                      // one-line "why this kind for this post"
}

export const IMAGE_BRIEF_KIND_LABEL: Record<ImageBriefKind, string> = {
  reference_news_image: 'Reference image',
  data_viz: 'Data visualisation',
  screenshot_quote: 'Screenshot quote',
  infographic: 'Infographic',
  diagram: 'Diagram',
}

export interface LinkedInDraft {
  id: string
  userId: string
  sourceCompanyId: string | null
  sourceKnowledgeId: string | null
  sourceCompanyName: string | null
  sourceUrl: string | null
  angle: LinkedInAngle
  hook: string
  body: string
  cta: string | null
  hashtags: string[]
  status: LinkedInDraftStatus
  critique: CritiquePayload | null
  polishRound: number
  scheduledFor: string | null
  postedAt: string | null
  postedUrl: string | null
  manualCopyAt: string | null
  engagementMetrics: EngagementMetrics | null
  whyItWorks?: string | null
  imageBrief: ImageBrief | null
  createdAt: string
  updatedAt: string
}

export interface LinkedInVoiceProfile {
  id: string
  userId: string
  profileMd: string
  toneDirectives: string
  avoidPhrases: string[]
  examplePosts: string[]
  createdAt: string
  updatedAt: string
}

export interface LinkedInScheduleSlot {
  dayOfWeek: number      // 0 = Sun .. 6 = Sat
  timeOfDay: string      // 'HH:MM:SS' or 'HH:MM'
  postsPerWeek: number
  pauseUntil?: string | null
  pausedReason?: string | null
}

export interface LinkedInPostingSchedule {
  slots: LinkedInScheduleSlot[]
  postsPerWeek: number
  nextSlot: string | null
}

/**
 * Aggregated stats shown in the /linkedin header strip.
 * The endpoint isn't built yet; we derive these in the page from the
 * drafts list until the API ships a dedicated `/linkedin/stats`.
 */
export interface LinkedInStats {
  pendingReview: number       // status='draft'
  scheduledThisWeek: number   // status in (approved, scheduled), scheduled_for in next 7d
  postedLastWeek: number      // status='posted', posted_at in last 7d
}

export const ANGLE_LABEL: Record<LinkedInAngle, string> = {
  news_commentary: 'News commentary',
  contrarian_take: 'Contrarian take',
  build_in_public: 'Build in public',
  lesson_learned: 'Lesson learned',
  industry_analysis: 'Industry analysis',
}

export const DAY_OF_WEEK_LABEL: Record<number, string> = {
  0: 'Sun',
  1: 'Mon',
  2: 'Tue',
  3: 'Wed',
  4: 'Thu',
  5: 'Fri',
  6: 'Sat',
}
