/**
 * /network — referral graph surface (P1.1).
 *
 * Replaces the "Coming soon" placeholder with the real V1 UI:
 *   - top: search + LinkedIn CSV import
 *   - middle: best warm intros (top 5 ranked paths) + target coverage
 *   - bottom: your network grouped by current company
 *
 * Today this page reads from the V1 mocks at dashboard/src/lib/mock/network.ts.
 * The `/network/*` endpoints in api/network.py are authored but not yet wired
 * into api/server.py — see api/NETWORK.md "How to wire the router" for the
 * one-line include. When that lands, swap MOCK_* below for fetch calls.
 *
 * Server Component owns the data hand-off; NetworkClient runs the
 * interactive bits (modal, filtering, file input).
 */
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { NetworkClient } from '@/components/network/NetworkClient'
import {
  MOCK_PEOPLE,
  MOCK_TARGET_COVERAGE,
  MOCK_TOP_INTRO_PATHS,
} from '@/lib/mock/network'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Network · Job Hunt',
  description:
    'Referral graph that finds the warmest path into your dream company.',
}

export default function NetworkPage() {
  // TODO: replace with parallel fetch of /network/target-coverage,
  //       /network/people, and a top-paths derivation once the router is
  //       wired into api/server.py — see api/NETWORK.md.
  const topPaths = MOCK_TOP_INTRO_PATHS
  const coverage = MOCK_TARGET_COVERAGE
  const people = MOCK_PEOPLE

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Sprint 2 · MVP"
        title="Network"
        description="Find the warmest path into your dream company. Top 1- and 2-hop intros across your target list."
      />

      <NetworkClient topPaths={topPaths} coverage={coverage} people={people} />
    </AppShell>
  )
}
