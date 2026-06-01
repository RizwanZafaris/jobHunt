/**
 * /jobs/rate — FRD-14 URL Job Rater.
 *
 * Paste a job URL (or the JD text) → instant 6-dimension fit rating, with an
 * opt-in "save to pipeline" promotion. All work happens client-side on demand
 * (fetch + extract + score are a single backend call), so this page is a thin
 * server wrapper around the interactive RateUrlClient.
 */
import { AppShell } from '@/components/layout/AppShell'
import RateUrlClient from './RateUrlClient'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Rate a Job · Job Hunt',
  description: 'Paste a job posting URL or description and get an instant 6-dimension fit rating.',
}

export default function RateJobPage() {
  return (
    <AppShell>
      <RateUrlClient />
    </AppShell>
  )
}
