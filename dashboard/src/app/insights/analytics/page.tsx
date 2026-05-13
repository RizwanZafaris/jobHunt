/**
 * /insights/analytics — Pattern Analytics dashboard.
 *
 * Surfaces the 6 read-only views shipped in migration 027:
 *   - Top-level funnel (applied → responded → … → accepted)
 *   - Three partitioned funnels (by letter_grade, archetype, company size)
 *   - Anomalous rejection-pattern clusters (lift >= 1.5x)
 *   - Per-company cost efficiency
 *
 * All sections render "Not enough data yet" placeholders below
 * sample-size thresholds rather than showing noisy graphs.
 *
 * Server Component: data is fetched in parallel server-side, surface
 * is non-interactive (no state, no client JS needed).
 */
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import {
  fetchFunnel,
  fetchFunnelByGrade,
  fetchFunnelByArchetype,
  fetchFunnelBySize,
  fetchRejectionPatterns,
  fetchCostEfficiency,
} from '@/lib/api'
import type {
  ConversionRates,
  CostEfficiencyResponse,
  FunnelResponse,
  PartitionedFunnelResponse,
  RejectionPatternsResponse,
} from '@/lib/types/analytics'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Analytics · Insights · Job Hunt',
  description:
    'Application funnel, rejection clusters, and per-company cost efficiency.',
}

// ── Fetch all 6 in parallel, tolerate any single failure ──────────────
type SettleResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string }

function settle<T>(p: Promise<T>): Promise<SettleResult<T>> {
  return p
    .then<SettleResult<T>>((value) => ({ ok: true, value }))
    .catch<SettleResult<T>>((err) => ({
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    }))
}

async function loadAll() {
  const [funnel, byGrade, byArch, bySize, rejPatterns, costEff] = await Promise.all([
    settle(fetchFunnel()),
    settle(fetchFunnelByGrade()),
    settle(fetchFunnelByArchetype()),
    settle(fetchFunnelBySize()),
    settle(fetchRejectionPatterns(5)),
    settle(fetchCostEfficiency(1)),
  ])

  return { funnel, byGrade, byArch, bySize, rejPatterns, costEff }
}

function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

function FunnelCard({ data }: { data: FunnelResponse }) {
  if (!data.is_sufficient_data) {
    return (
      <p className="text-2xs text-fg-subtle">{data.message || 'No data yet.'}</p>
    )
  }
  const stages: Array<[keyof FunnelResponse, string]> = [
    ['applied', 'Applied'],
    ['responded', 'Responded'],
    ['interviewed', 'Interviewed'],
    ['offered', 'Offered'],
    ['accepted', 'Accepted'],
  ]
  return (
    <div className="space-y-2">
      <ul className="grid grid-cols-5 gap-2 text-center text-2xs">
        {stages.map(([k, label]) => (
          <li
            key={k as string}
            className="rounded-md border border-border bg-card px-2 py-1.5"
          >
            <div className="text-fg-subtle">{label}</div>
            <div className="font-mono tabular-nums text-fg">
              {data[k] as number}
            </div>
          </li>
        ))}
      </ul>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-3xs text-fg-subtle sm:grid-cols-3">
        {(Object.entries(data.conversion_rates) as Array<[keyof ConversionRates, number]>).map(
          ([k, v]) => (
            <li key={k}>
              <span className="font-mono tabular-nums text-fg-muted">{pct(v)}</span>{' '}
              {String(k).replaceAll('_', ' ')}
            </li>
          ),
        )}
      </ul>
    </div>
  )
}

function PartitionedCard({ data }: { data: PartitionedFunnelResponse }) {
  if (!data.is_sufficient_data || data.partitions.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle">{data.message || 'No data yet.'}</p>
    )
  }
  return (
    <table className="w-full text-2xs">
      <thead className="text-fg-subtle">
        <tr>
          <th className="text-left font-normal">Key</th>
          <th className="text-right font-normal">Applied</th>
          <th className="text-right font-normal">Resp</th>
          <th className="text-right font-normal">Intv</th>
          <th className="text-right font-normal">Offer</th>
          <th className="text-right font-normal">A→O</th>
        </tr>
      </thead>
      <tbody className="text-fg">
        {data.partitions.map((p) => (
          <tr key={p.key}>
            <td className="py-0.5 font-mono text-3xs">{p.key}</td>
            <td className="py-0.5 text-right tabular-nums">{p.applied}</td>
            <td className="py-0.5 text-right tabular-nums">{p.responded}</td>
            <td className="py-0.5 text-right tabular-nums">{p.interviewed}</td>
            <td className="py-0.5 text-right tabular-nums">{p.offered}</td>
            <td className="py-0.5 text-right tabular-nums text-fg-muted">
              {pct(p.conversion_rates.applied_to_offered)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function RejectionsCard({ data }: { data: RejectionPatternsResponse }) {
  if (!data.is_sufficient_data || data.patterns.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle">{data.message || 'No clusters yet.'}</p>
    )
  }
  return (
    <table className="w-full text-2xs">
      <thead className="text-fg-subtle">
        <tr>
          <th className="text-left font-normal">Dimension</th>
          <th className="text-left font-normal">Value</th>
          <th className="text-right font-normal">N</th>
          <th className="text-right font-normal">Rate</th>
          <th className="text-right font-normal">Lift</th>
          <th className="text-right font-normal">Avg days</th>
        </tr>
      </thead>
      <tbody className="text-fg">
        {data.patterns.map((p) => (
          <tr key={`${p.dimension}-${p.value}`}>
            <td className="py-0.5 font-mono text-3xs">{p.dimension}</td>
            <td className="py-0.5">{p.value}</td>
            <td className="py-0.5 text-right tabular-nums">{p.n}</td>
            <td className="py-0.5 text-right tabular-nums">{pct(p.rate)}</td>
            <td className="py-0.5 text-right tabular-nums text-amber-300">
              {p.lift.toFixed(2)}×
            </td>
            <td className="py-0.5 text-right tabular-nums text-fg-muted">
              {p.avg_days_to_rejection !== null
                ? p.avg_days_to_rejection.toFixed(1)
                : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CostEfficiencyCard({ data }: { data: CostEfficiencyResponse }) {
  if (data.rows.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle">{data.message || 'No spend recorded.'}</p>
    )
  }
  return (
    <table className="w-full text-2xs">
      <thead className="text-fg-subtle">
        <tr>
          <th className="text-left font-normal">Company</th>
          <th className="text-right font-normal">Spend</th>
          <th className="text-right font-normal">Calls</th>
          <th className="text-right font-normal">Intvs</th>
          <th className="text-right font-normal">Offers</th>
          <th className="text-right font-normal">$/outcome</th>
        </tr>
      </thead>
      <tbody className="text-fg">
        {data.rows.map((r) => (
          <tr key={r.company_name}>
            <td className="py-0.5 font-mono text-3xs">{r.company_name}</td>
            <td className="py-0.5 text-right tabular-nums">
              ${r.total_cost_usd.toFixed(2)}
            </td>
            <td className="py-0.5 text-right tabular-nums">{r.total_calls}</td>
            <td className="py-0.5 text-right tabular-nums">{r.interviews}</td>
            <td className="py-0.5 text-right tabular-nums">{r.offers}</td>
            <td className="py-0.5 text-right tabular-nums text-fg-muted">
              {r.cost_per_outcome !== null
                ? `$${r.cost_per_outcome.toFixed(2)}`
                : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default async function AnalyticsPage() {
  const data = await loadAll()

  return (
    <AppShell>
      <PageHeader
        eyebrow="Insights"
        title="Pattern analytics"
        description="Application funnel + rejection clusters + per-company cost efficiency. Shows 'Not enough data yet' below 5 applications / 5 rejections to keep the surface honest."
      />

      <section className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold text-fg">Top-level funnel</h2>
        {data.funnel.ok ? (
          <FunnelCard data={data.funnel.value as FunnelResponse} />
        ) : (
          <p className="text-2xs text-rose-300">
            Live data unavailable: {data.funnel.ok ? '' : data.funnel.error}
          </p>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <section className="space-y-2 rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold text-fg">By grade</h2>
          {data.byGrade.ok ? (
            <PartitionedCard data={data.byGrade.value as PartitionedFunnelResponse} />
          ) : (
            <p className="text-2xs text-rose-300">
              {data.byGrade.ok ? '' : data.byGrade.error}
            </p>
          )}
        </section>
        <section className="space-y-2 rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold text-fg">By archetype</h2>
          {data.byArch.ok ? (
            <PartitionedCard data={data.byArch.value as PartitionedFunnelResponse} />
          ) : (
            <p className="text-2xs text-rose-300">
              {data.byArch.ok ? '' : data.byArch.error}
            </p>
          )}
        </section>
        <section className="space-y-2 rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold text-fg">By company size</h2>
          {data.bySize.ok ? (
            <PartitionedCard data={data.bySize.value as PartitionedFunnelResponse} />
          ) : (
            <p className="text-2xs text-rose-300">
              {data.bySize.ok ? '' : data.bySize.error}
            </p>
          )}
        </section>
      </div>

      <section className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold text-fg">Rejection clusters (≥1.5× lift)</h2>
        {data.rejPatterns.ok ? (
          <RejectionsCard data={data.rejPatterns.value as RejectionPatternsResponse} />
        ) : (
          <p className="text-2xs text-rose-300">
            {data.rejPatterns.ok ? '' : data.rejPatterns.error}
          </p>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold text-fg">Cost efficiency by company</h2>
        {data.costEff.ok ? (
          <CostEfficiencyCard data={data.costEff.value as CostEfficiencyResponse} />
        ) : (
          <p className="text-2xs text-rose-300">
            {data.costEff.ok ? '' : data.costEff.error}
          </p>
        )}
      </section>
    </AppShell>
  )
}
