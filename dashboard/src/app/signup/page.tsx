/**
 * /signup - Google OAuth sign-up (Phase 4, P4-2).
 *
 * With Google OAuth there is no separate credential creation step - "sign up"
 * and "sign in" are the same OAuth call; a first-time Google user simply gets a
 * new Supabase auth user (and, on first reach of the backend, a users row).
 * This page is the marketing-framed entry; it routes new users through
 * onboarding by pointing `next` at /onboarding.
 */
'use client'

import { Suspense, useState } from 'react'
import { getSupabaseBrowserClient } from '@/lib/supabase/client'
import { Button } from '@/components/ui/Button'

function SignupInner() {
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function signUpWithGoogle() {
    setError(null)
    setLoading(true)
    const supabase = getSupabaseBrowserClient()
    if (!supabase) {
      setError('Authentication is not configured for this deployment.')
      setLoading(false)
      return
    }
    // New users land in onboarding after the callback exchanges the session.
    const redirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent('/onboarding')}`
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo },
    })
    if (error) {
      setError(error.message)
      setLoading(false)
    }
  }

  return (
    <main
      id="main"
      className="min-h-screen flex items-center justify-center bg-bg px-4"
    >
      <div className="w-full max-w-sm rounded-xl border border-border bg-surface p-8 shadow-sm">
        <h1 className="text-lg font-semibold text-fg">Create your jobHunt account</h1>
        <p className="mt-1 text-xs text-fg-muted">
          Let AI run your job search. Get started in seconds.
        </p>

        <Button
          variant="primary"
          size="lg"
          className="mt-6 w-full justify-center"
          onClick={signUpWithGoogle}
          disabled={loading}
        >
          {loading ? 'Redirecting...' : 'Sign up with Google'}
        </Button>

        {error && (
          <p className="mt-4 text-2xs text-danger" role="alert">
            {error}
          </p>
        )}

        <p className="mt-6 text-2xs text-fg-subtle">
          Already have an account?{' '}
          <a href="/login" className="text-accent hover:underline">
            Sign in
          </a>
        </p>
      </div>
    </main>
  )
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupInner />
    </Suspense>
  )
}
