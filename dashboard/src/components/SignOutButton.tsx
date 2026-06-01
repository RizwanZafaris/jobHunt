/**
 * SignOutButton — ends the Supabase session (Phase 4, P4-2c).
 *
 * Client component: signs out via the browser client (clears the auth cookies)
 * then navigates to /login. Renders nothing in single-user mode (default), so
 * the live dashboard is unchanged until multi-tenant is explicitly enabled.
 *
 * Drop it into the dashboard chrome (e.g. a header/menu) wherever a sign-out
 * affordance belongs; it's intentionally self-contained.
 */
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { getSupabaseBrowserClient, isMultiTenantMode } from '@/lib/supabase/client'
import { Button } from '@/components/ui/Button'

export function SignOutButton({
  className,
}: {
  className?: string
}) {
  const router = useRouter()
  const [busy, setBusy] = useState(false)

  // Single-user mode (default): no session to end — render nothing so prod is
  // byte-for-byte unchanged.
  if (!isMultiTenantMode()) return null

  async function signOut() {
    setBusy(true)
    const supabase = getSupabaseBrowserClient()
    try {
      await supabase?.auth.signOut()
    } catch {
      /* even if the network call fails, send the user to /login */
    }
    router.push('/login')
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      className={className}
      onClick={signOut}
      disabled={busy}
    >
      {busy ? 'Signing out…' : 'Sign out'}
    </Button>
  )
}

export default SignOutButton
