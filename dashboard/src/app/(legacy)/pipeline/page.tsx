/**
 * /pipeline → /today (legacy).
 *
 * The (legacy) route group keeps this file out of the nav while still
 * answering links from older bookmarks, emails, and any AI-generated
 * docs that referenced /pipeline as the home.
 */
import { redirect } from 'next/navigation'

export default function LegacyPipelineRedirect(): never {
  redirect('/today')
}
