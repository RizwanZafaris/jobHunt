/**
 * System insight surface — the Boss agent chat.
 *
 * Extracted from app/boss/page.tsx so /insights can host it under the
 * System sub-tab. Boss is the strategic orchestrator with live access to
 * the pipeline; the chat surface is the human entrypoint.
 */
import BossChat from '@/components/BossChat'

export function System() {
  return (
    <div className="space-y-4">
      <p className="text-sm text-fg-muted leading-relaxed max-w-2xl">
        Ask the strategic orchestrator anything. Live access to your top jobs,
        recent builds, last-24h cost, and active applications.
      </p>
      <BossChat />
    </div>
  )
}

export default System
