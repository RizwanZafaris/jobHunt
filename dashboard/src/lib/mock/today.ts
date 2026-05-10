/**
 * Mock TodayAction list — used until /actions/today ships.
 *
 * Mixes all six kinds so the page renders representatively in development.
 * The order here is the rendered order; the real endpoint will rank by
 * a composite score (state weight × recency × company priority).
 *
 * TODO: replace with /actions/today endpoint — see _pending_endpoints.md
 */

import type { TodayAction } from '@/lib/types/today'

export const MOCK_TODAY_ACTIONS: TodayAction[] = [
  {
    // Approved-and-scheduled-for-today LinkedIn post — top of stack.
    // The action's primary CTA is "Copy and post on LinkedIn"; the user
    // copies via the dashboard, pastes into LinkedIn, and the API records
    // manual_copy_at + flips status='posted'.
    //
    // TODO: replace with /actions/today result that joins linkedin_drafts
    //       on (status in ('approved','scheduled') AND scheduled_for::date = today)
    //       — see api/LINKEDIN.md and _pending_endpoints.md.
    id: 'mock-linkedin-today',
    kind: 'linkedin_post_due',
    title: 'Copy and post on LinkedIn — Stripe Payment Element take',
    subtitle:
      'Approved draft, scheduled 09:00. "Stripe’s new Payment Element abstracts away 23 payment methods. I shipped 4 with my team — here’s the part nobody warns you about."',
    state: 'ready',
    primary: { label: 'Copy and post on LinkedIn', href: '/linkedin' },
    secondary: { label: 'Edit before posting', href: '/linkedin' },
    meta: { date: '2026-05-10' },
  },
  {
    id: 'mock-1',
    kind: 'resume_ready',
    title: 'Apply to Anthropic — Solutions Engineer',
    subtitle: 'Resume v3 converged at polisher score 92. Ready to send.',
    state: 'ready',
    primary: { label: 'Open application portal', href: '/jobs/anthropic-se' },
    secondary: { label: 'View resume', href: '/jobs/anthropic-se/resume' },
    meta: { score: 92, company: 'Anthropic' },
  },
  {
    id: 'mock-2',
    kind: 'score_high_no_resume',
    title: 'Build resume for OpenAI — Forward Deployed Engineer',
    subtitle: 'Scored 88 at evaluation. No resume build yet.',
    state: 'pending',
    primary: { label: 'Kick off G2 build', onClick: 'kickoff_g2' },
    secondary: { label: 'View JD', href: '/jobs/openai-fde' },
    meta: { score: 88, company: 'OpenAI' },
  },
  {
    id: 'mock-3',
    kind: 'stale_application',
    title: 'Log outcome — Stripe SE (applied 9 days ago)',
    subtitle: 'No reply heard. Mark as silent / interview / rejected.',
    state: 'stale',
    primary: { label: 'Log outcome', onClick: 'log_outcome' },
    secondary: { label: 'Open thread', href: '/applications/stripe-se' },
    meta: { company: 'Stripe', date: '2026-05-01' },
  },
  {
    id: 'mock-4',
    kind: 'score_below_threshold',
    title: 'Datadog — Sales Engineer scored 78 (below 85)',
    subtitle: 'Skip, or adjust your ICP weights to widen the funnel.',
    state: 'blocked',
    primary: { label: 'Review reasoning', href: '/jobs/datadog-se' },
    secondary: { label: 'Tune scoring', href: '/profile/keywords' },
    meta: { score: 78, company: 'Datadog' },
  },
  {
    id: 'mock-5',
    kind: 'persona_stale',
    title: 'Refresh persona — Vercel (last updated 18 days ago)',
    subtitle: 'Sunday cron missed; news + recent outcomes need re-synthesis.',
    state: 'stale',
    primary: { label: 'Refresh now', href: '/insights?tab=personas&refresh=vercel' },
    meta: { company: 'Vercel', date: '2026-04-22' },
  },
]
