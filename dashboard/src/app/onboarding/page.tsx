/**
 * /onboarding - first-run flow (Phase 4, P4-2c).
 *
 * A lightweight 3-step welcome that completes by POSTing to the backend through
 * the existing API proxy (/api/proxy/me/onboarding -> POST /me/onboarding, P4-1).
 * Completing stamps users.onboarded_at so the user is never routed here again.
 *
 * Deliberately minimal (welcome -> how-it-works -> finish). Profile/resume
 * import and plan selection are richer follow-ups; this is the front-door
 * minimum that makes a brand-new user land somewhere intentional instead of a
 * cold dashboard.
 */
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/Button'

const STEPS = [
  {
    key: 'welcome',
    title: 'Welcome to jobHunt',
    body: 'Your AI co-pilot for the whole search - discovery, tailored resumes, interview prep, and warm intros. Let us get you set up.',
  },
  {
    key: 'how',
    title: 'How it works',
    body: 'Every day, jobHunt scouts roles that fit you, scores them, and drafts what you need to apply. You stay in control - review, edit, and send.',
  },
  {
    key: 'ready',
    title: 'You are ready',
    body: 'We will take you to Today - your daily set of recommended roles. You can refine your profile any time from Settings.',
  },
] as const

export default function OnboardingPage() {
  const router = useRouter()
  const [i, setI] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const step = STEPS[i]
  const isLast = i === STEPS.length - 1

  async function persistStep(stepKey: string, complete: boolean) {
    // Best-effort cursor persistence; non-blocking for intermediate steps.
    try {
      await fetch('/api/proxy/me/onboarding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: stepKey, complete }),
      })
    } catch {
      /* intermediate step persistence is best-effort */
    }
  }

  async function next() {
    setError(null)
    if (!isLast) {
      void persistStep(STEPS[i + 1].key, false)
      setI(i + 1)
      return
    }
    // Final step -> complete onboarding, then go to the app.
    setBusy(true)
    try {
      const res = await fetch('/api/proxy/me/onboarding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: 'done', complete: true }),
      })
      if (!res.ok) {
        setError('Could not finish setup. Please try again.')
        setBusy(false)
        return
      }
      router.push('/today')
    } catch {
      setError('Network error finishing setup. Please try again.')
      setBusy(false)
    }
  }

  return (
    <main
      id="main"
      className="min-h-screen flex items-center justify-center bg-bg px-4"
    >
      <div className="w-full max-w-md rounded-xl border border-border bg-surface p-8 shadow-sm">
        {/* progress dots */}
        <div className="flex gap-1.5" aria-hidden="true">
          {STEPS.map((s, idx) => (
            <span
              key={s.key}
              className={
                'h-1.5 flex-1 rounded-full ' +
                (idx <= i ? 'bg-accent' : 'bg-border')
              }
            />
          ))}
        </div>

        <h1 className="mt-6 text-lg font-semibold text-fg">{step.title}</h1>
        <p className="mt-2 text-sm text-fg-muted">{step.body}</p>

        {error && (
          <p className="mt-4 text-2xs text-danger" role="alert">
            {error}
          </p>
        )}

        <div className="mt-8 flex items-center justify-between">
          <span className="text-2xs text-fg-subtle">
            Step {i + 1} of {STEPS.length}
          </span>
          <Button variant="primary" size="md" onClick={next} disabled={busy}>
            {busy ? 'Finishing...' : isLast ? 'Go to dashboard' : 'Continue'}
          </Button>
        </div>
      </div>
    </main>
  )
}
