/**
 * /login — Google OAuth sign-in (Phase 4, P4-2).
 *
 * Client component: kicks off Supabase's Google OAuth flow. Supabase redirects
 * to Google, Google back to Supabase's /auth/v1/callback, and Supabase finally
 * to OUR /auth/callback route (redirectTo below) which exchanges the code for a
 * session cookie and lands the user in the app.
 *
 * Renders a clear message if Supabase env isn't configured (preview misconfig)
 * rather than throwing.
 */
'use client'

import { Suspense, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { getSupabaseBrowserClient } from '@/lib/supabase/client'
import { Button } from '@/components/ui/Button'

function LoginInner() {
  const params = useSearchParams()
  const next = params.get('next') || '/today'
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function signInWithGoogle() {
    setError(null)
    setLoading(true)
    const supabase = getSupabaseBrowserClient()
    if (!supabase) {
      setError('Authentication is not configured for this deployment.')
      setLoading(false)
      return
    }
    const redirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo },
    })
    if (error) {
      setError(error.message)
      setLoading(false)
    }
    // On success the browser is navigating to Google; no state to reset.
  }

  return (
    <main
      id="main"
      className="min-h-screen flex items-center justify-center bg-bg px-4"
    >
      <div className="w-full max-w-sm rounded-xl border border-border bg-surface p-8 shadow-sm">
        <h1 className="text-lg font-semibold text-fg">Sign in to jobHunt</h1>
        <p className="mt-1 text-xs text-fg-muted">
          Your AI job-search co-pilot.
        </p>

        <Button
          variant="secondary"
          size="lg"
          className="mt-6 w-full justify-center"
          onClick={signInWithGoogle}
          disabled={loading}
        >
          {loading ? 'Redirecting…' : 'Continue with Google'}
        </Button>

        {error && (
          <p className="mt-4 text-2xs text-danger" role="alert">
            {error}
          </p>
        )}

        <p className="mt-6 text-2xs text-fg-subtle">
          New here?{' '}
          <a href="/signup" className="text-accent hover:underline">
            Create an account
          </a>
        </p>
      </div>
    </main>
  )
}

export default function LoginPage() {
  // useSearchParams requires a Suspense boundary in Next 15.
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  )
}
