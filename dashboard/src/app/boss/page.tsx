/**
 * /boss — interactive chat with the Boss agent.
 *
 * The BossAgent is the nightly orchestrator that audits the pipeline.
 * This page lets you talk to that same persona ad-hoc, with live
 * pipeline state injected into the system prompt so answers are
 * grounded in real data (top jobs, recent builds, 24h cost rollup,
 * applications by status).
 *
 * Each turn ~$0.05–$0.15 (Claude Opus 4.5, ≤1500 output tokens).
 */
import BossChat from '@/components/BossChat'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Boss · Job Hunt AI',
  description:
    'Chat with the Boss agent — strategic pipeline questions, grounded in live state.',
}

export default function BossPage() {
  return (
    <AppShell>
      <PageHeader
        eyebrow="Pipeline · Strategy"
        title="Boss"
        description="Ask the strategic orchestrator anything. Live access to your top jobs, recent builds, last-24h cost, and active applications."
      />
      <div className="mt-6">
        <BossChat />
      </div>
    </AppShell>
  )
}
