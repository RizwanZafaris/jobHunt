/**
 * /targets → /companies.
 *
 * The new IA exposes "Targets" in the nav; the existing implementation
 * is at /companies. Redirect keeps both URLs live so we can rename the
 * underlying route folder later without breaking anything.
 *
 * TODO: rename app/companies → app/targets once all internal links
 * (jobs/[id], digest deep links, etc.) are migrated.
 */
import { redirect } from 'next/navigation'

export default function TargetsRedirect(): never {
  redirect('/companies')
}
