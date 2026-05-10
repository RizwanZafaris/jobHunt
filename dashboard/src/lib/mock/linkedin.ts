/**
 * Mock data for the LinkedIn presence engine.
 *
 * TODO: replace with API once /linkedin/* is wired into api/server.py.
 * Until then this file feeds dashboard/src/app/linkedin/page.tsx and the
 * <DraftCard /> stories.
 */

import type {
  LinkedInDraft,
  LinkedInPostingSchedule,
  LinkedInStats,
  LinkedInVoiceProfile,
} from '@/lib/types/linkedin'

// TODO: replace with API
export const MOCK_VOICE_PROFILE: LinkedInVoiceProfile = {
  id: 'mock-voice-1',
  userId: '00000000-0000-0000-0000-000000000001',
  profileMd:
    'Senior Product / Programme Lead, 14+ years in fintech and payments. Scaled SimPaisa from $0 to $1B+ TPV; led 40 engineers; reduced auth failure 8% → 1.2%; shipped BNPL to 100K users in 8 months. Direct, plainspoken, lets the numbers do the talking.',
  toneDirectives:
    'plainspoken, specific, opinionated; never humble-brags; uses concrete metrics; avoids em-dashes-everywhere AI tells',
  avoidPhrases: [
    'delve',
    'tapestry',
    'unpack',
    'journey',
    'at the end of the day',
    'a testament to',
    'navigate the complexities',
    "in today's fast-paced world",
  ],
  examplePosts: [],
  createdAt: '2026-05-08T10:00:00Z',
  updatedAt: '2026-05-08T10:00:00Z',
}

// TODO: replace with API
export const MOCK_POSTING_SCHEDULE: LinkedInPostingSchedule = {
  slots: [
    { dayOfWeek: 1, timeOfDay: '09:00:00', postsPerWeek: 3 },
    { dayOfWeek: 3, timeOfDay: '09:00:00', postsPerWeek: 3 },
    { dayOfWeek: 5, timeOfDay: '09:00:00', postsPerWeek: 3 },
  ],
  postsPerWeek: 3,
  nextSlot: '2026-05-11T09:00:00Z',
}

// TODO: replace with API
export const MOCK_DRAFTS: LinkedInDraft[] = [
  {
    id: 'mock-draft-1',
    userId: '00000000-0000-0000-0000-000000000001',
    sourceCompanyId: 'mock-co-marqeta',
    sourceCompanyName: 'Marqeta',
    sourceKnowledgeId: 'mock-ck-1',
    sourceUrl: 'https://www.marqeta.com/newsroom/credit-launch',
    angle: 'contrarian_take',
    hook:
      "Everyone's calling Marqeta's credit pivot a defensive move.\n\nIt's the opposite. Here's what they actually saw before anyone else.",
    body:
      "Issuer-processors don't get repricing power on debit. The interchange ceiling is fixed; volume growth is the only lever, and the BIN sponsor banks are catching up.\n\nCredit is different. The merchant fee is 2-3x debit. Underwriting risk is yours, but so is the upside. Marqeta has seven years of cardholder spend data — they know who pays back faster than the FICO file does.\n\nI ran a similar bet at SimPaisa in 2023. We added card credit on top of a wallet that had cleared $400M TPV. The unit economics flipped: a debit txn earned us 14 bps, a revolving credit account earned us $42 in year one. Same customer.\n\nThe 'pivot' framing is backwards. Marqeta isn't reacting to slowing debit growth — they're cashing in seven years of data they already paid to collect. Everyone else processed those swipes and threw the data away.\n\nThe contrarian read: this isn't a moonshot, it's a margin defense disguised as one.",
    cta: "Curious — anyone in the issuer space seeing the same shift in their roadmap?",
    hashtags: ['#payments', '#fintech', '#productmanagement'],
    status: 'draft',
    critique: {
      issues: [
        {
          severity: 'P1',
          kind: 'cta_quality',
          span: 'Curious — anyone in the issuer space',
          fix: "Tighten to a specific question: 'Is anyone seeing this in their P&L yet?'",
        },
      ],
      passes: ['no_humble_brag', 'specific_numbers', 'one_em_dash'],
      verdict: 'ship_after_fix',
    },
    polishRound: 1,
    scheduledFor: null,
    postedAt: null,
    postedUrl: null,
    manualCopyAt: null,
    engagementMetrics: null,
    whyItWorks:
      'Opens with a sharp counter-claim, then cashes it with a specific number from the user’s SimPaisa run. The "everyone vs me" frame is contrarian without being smug.',
    createdAt: '2026-05-10T08:00:00Z',
    updatedAt: '2026-05-10T08:00:00Z',
  },
  {
    id: 'mock-draft-2',
    userId: '00000000-0000-0000-0000-000000000001',
    sourceCompanyId: 'mock-co-stripe',
    sourceCompanyName: 'Stripe',
    sourceKnowledgeId: 'mock-ck-2',
    sourceUrl: 'https://stripe.com/blog/payment-element-2026',
    angle: 'lesson_learned',
    hook:
      "Stripe's new Payment Element abstracts away 23 payment methods.\n\nI shipped something similar at SimPaisa with 4. Here's the part nobody warns you about.",
    body:
      "Adding a payment method looks like a backend job. It isn't. Each new method drags 6-8 weeks of unglamorous work that nobody scopes:\n\n– Three settlement reconciliations that don't agree (acquirer, scheme, your ledger).\n– A dispute pipeline that has to handle 2-3 chargeback codes per network.\n– A customer-support flow because users will get charged twice when our retry logic and the network's retry logic both fire.\n– A KYC delta because BNPL creditors see one regulatory form, card schemes see another.\n\nWe shipped 4 methods in 8 months at SimPaisa. The third one took the same time as the first two combined.\n\nStripe's bet here isn't on the UI — it's on the operational tail. If they've genuinely flattened that for 23 methods, the moat is the ops, not the API.",
    cta: 'For the PMs who own payment-method coverage: what was the hidden cost in your last integration?',
    hashtags: ['#payments', '#fintech', '#productlessons'],
    status: 'approved',
    critique: null,
    polishRound: 1,
    scheduledFor: '2026-05-11T09:00:00Z',
    postedAt: null,
    postedUrl: null,
    manualCopyAt: null,
    engagementMetrics: null,
    whyItWorks:
      'Frames a high-status news event around a war story only the user could tell. The "third method" beat is a pattern recognition reveal; cheap and authentic.',
    createdAt: '2026-05-09T14:00:00Z',
    updatedAt: '2026-05-09T16:00:00Z',
  },
  {
    id: 'mock-draft-3',
    userId: '00000000-0000-0000-0000-000000000001',
    sourceCompanyId: null,
    sourceCompanyName: null,
    sourceKnowledgeId: null,
    sourceUrl: null,
    angle: 'industry_analysis',
    hook:
      "PayPal's Q1 takeaway is buried on slide 14: branded checkout grew 6%, unbranded grew 22%.\n\nThe entire industry is misreading what this means.",
    body:
      "Branded checkout is the conversion event a consumer recognises (PayPal button). Unbranded is everything they DON'T see: invisible card processing on a thousand merchant websites.\n\nThe consensus read: 'PayPal is winning unbranded share.' That's wrong. PayPal isn't winning unbranded share — Shopify and Stripe are forcing every PSP into a race-to-the-bottom on infrastructure pricing, and PayPal is participating in that race because they have to.\n\nThe real story: branded checkout — the thing PayPal can charge a premium for — is decelerating. The 22% unbranded growth is a margin sink, not a moat.\n\nWatch what happens to PayPal's take rate next two quarters. If it doesn't compress, I'm wrong.",
    cta: 'Anyone in payments earnings — what does your take rate look like quarter-over-quarter?',
    hashtags: ['#payments', '#fintech'],
    status: 'posted',
    critique: null,
    polishRound: 1,
    scheduledFor: '2026-05-08T09:00:00Z',
    postedAt: '2026-05-08T09:14:00Z',
    postedUrl: 'https://linkedin.com/posts/rizwanzafar/example-mock-1',
    manualCopyAt: '2026-05-08T09:12:00Z',
    engagementMetrics: { likes: 47, comments: 9, views: 2310, recordedAt: '2026-05-09T18:00:00Z' },
    whyItWorks:
      'Buries-the-lede framing forces a re-read of the news. Picks a falsifiable prediction so the post invites disagreement, not just nods.',
    createdAt: '2026-05-07T16:00:00Z',
    updatedAt: '2026-05-08T09:14:00Z',
  },
]

/**
 * Compute summary stats from the drafts list.
 * Will be replaced by a dedicated /linkedin/stats endpoint later.
 */
export function deriveLinkedInStats(drafts: LinkedInDraft[]): LinkedInStats {
  const now = new Date()
  const weekAhead = new Date(now)
  weekAhead.setDate(weekAhead.getDate() + 7)
  const weekAgo = new Date(now)
  weekAgo.setDate(weekAgo.getDate() - 7)

  return {
    pendingReview: drafts.filter((d) => d.status === 'draft').length,
    scheduledThisWeek: drafts.filter((d) => {
      if (d.status !== 'approved' && d.status !== 'scheduled') return false
      if (!d.scheduledFor) return false
      const t = new Date(d.scheduledFor)
      return t >= now && t <= weekAhead
    }).length,
    postedLastWeek: drafts.filter((d) => {
      if (d.status !== 'posted' || !d.postedAt) return false
      const t = new Date(d.postedAt)
      return t >= weekAgo && t <= now
    }).length,
  }
}

// TODO: replace with API
export const MOCK_LINKEDIN_STATS: LinkedInStats = deriveLinkedInStats(MOCK_DRAFTS)
