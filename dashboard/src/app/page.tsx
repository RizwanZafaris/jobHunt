/**
 * Root → /today.
 *
 * The Pipeline-as-home metaphor was engineer-shaped. The job-seeker home
 * is the ranked action list at /today.
 */
import { redirect } from 'next/navigation'

export default function RootRedirect(): never {
  redirect('/today')
}
