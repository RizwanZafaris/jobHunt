/**
 * /personas → /insights?tab=personas (back-compat).
 *
 * Personas now live inside the Insights tab strip alongside Costs and
 * System. Bookmarks and existing links keep working via this redirect.
 */
import { redirect } from 'next/navigation'

export default function PersonasRedirect(): never {
  redirect('/insights?tab=personas')
}
