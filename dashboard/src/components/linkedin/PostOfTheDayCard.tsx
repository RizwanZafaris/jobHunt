/**
 * PostOfTheDayCard — surfaces the latest LinkedIn draft on /today + /applications.
 *
 * Stream D (2026-05-13). User feedback: "Today page shows section is just
 * shows old job there is no today's linkedin post that should be there."
 *
 * Server component (data is pre-fetched by the page). Renders a prominent
 * card with the hook + body excerpt + a CTA to go to /linkedin for the
 * full draft + approval flow.
 */
import Link from 'next/link'
import type { PostOfTheDay } from '@/lib/api'

interface Props {
  post: PostOfTheDay | null
  emptyCta?: 'generate' | 'hide'
}

function statusBadge(status: string): { label: string; cls: string } {
  switch (status) {
    case 'approved':
      return { label: 'Approved · scheduled', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' }
    case 'scheduled':
      return { label: 'Scheduled for today', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' }
    case 'pending_review':
      return { label: 'Needs review', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40' }
    case 'draft':
    default:
      return { label: 'Draft', cls: 'bg-sky-500/15 text-sky-300 border-sky-500/40' }
  }
}

export function PostOfTheDayCard({ post, emptyCta = 'generate' }: Props) {
  if (!post) {
    if (emptyCta === 'hide') return null
    return (
      <section className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-fg">Today&apos;s LinkedIn post</h3>
          <code className="text-3xs text-fg-subtle font-mono">linkedin_drafts</code>
        </div>
        <p className="mt-1 text-2xs text-fg-subtle">
          No draft pending. Generate one to keep your weekly cadence on track.
        </p>
        <Link
          href="/linkedin"
          className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-2xs font-semibold text-accent-fg hover:bg-accent-hover min-h-9"
        >
          Go to LinkedIn →
        </Link>
      </section>
    )
  }

  const badge = statusBadge(post.status)
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">Today&apos;s LinkedIn post</h3>
        <span className={`rounded border px-1.5 py-0.5 text-3xs ${badge.cls}`}>
          {badge.label}
        </span>
      </div>
      {post.company && (
        <p className="mt-1 text-3xs text-fg-subtle font-mono uppercase tracking-wider">
          {post.company} · {post.angle?.replaceAll('_', ' ') || 'general'}
        </p>
      )}
      <p className="mt-2 text-sm font-semibold text-fg">{post.hook}</p>
      {post.body_excerpt && (
        <p className="mt-1.5 text-2xs text-fg-muted line-clamp-3">
          {post.body_excerpt}…
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Link
          href="/linkedin"
          className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-2xs font-semibold text-accent-fg hover:bg-accent-hover min-h-9"
        >
          {post.status === 'draft' ? 'Review & approve →' : 'Open draft →'}
        </Link>
        <Link
          href="/linkedin"
          className="text-2xs text-fg-muted hover:text-fg"
        >
          See all drafts
        </Link>
      </div>
    </section>
  )
}
