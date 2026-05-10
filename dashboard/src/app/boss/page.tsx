/**
 * /boss → /insights?tab=system (back-compat).
 *
 * Boss is now branded as "System" inside Insights — the same chat
 * surface, just relabelled to make the IA legible to job seekers.
 */
import { redirect } from 'next/navigation'

export default function BossRedirect(): never {
  redirect('/insights?tab=system')
}
