/**
 * Analytics insight surface (Audit Gap §4.3).
 *
 * Embedded inside the /insights tabbed shell. Pulls data from 6
 * /analytics/* endpoints in parallel and renders honest views — always
 * shows the *real* underlying numbers, never a hardcoded placeholder.
 *
 * Why the numbers can look small at low scale
 * -------------------------------------------
 * Below MIN_APPLICATIONS_FOR_FUNNEL=5, the API returns
 * `is_sufficient_data: false` so the dashboard doesn't draw misleading
 * conversion-rate graphs. We STILL surface the raw counts + the source
 * view name so the user can see exactly which row in which view the
 * number came from. No placeholder text replaces real data.
 */
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

// ── Live-data signal ───────────────────────────────────────────────────
function FreshnessBar({ at }: { at: Date }) {
  const iso = at.toISOString().replace('T', ' ').slice(0, 19)
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 text-3xs text-fg-subtle">
      <span>
        <span className="font-mono tabular-nums text-fg-muted">Live</span>{' '}
        from{' '}
        <code className="font-mono">v_application_funnel</code>,{' '}
        <code className="font-mono">v_application_funnel_by_grade</code>,{' '}
        <code className="font-mono">_by_archetype</code>,{' '}
        <code className="font-mono">_by_company_size</code>,{' '}
        <code className="font-mono">v_rejection_signals</code>,{' '}
        <code className="font-mono">v_cost_per_outcome</code>
      </span>
      <span className="opacity-60">·</span>
      <span>
        Loaded{' '}
        <time className="font-mono tabular-nums text-fg-muted" dateTime={at.toISOString()}>
          {iso} UTC
        </time>
      </span>
    </div>
  )
}

// ── Funnel card with raw-number proof ──────────────────────────────────
function FunnelCard({ data }: { data: FunnelResponse }) {
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
            <div className="font-mono tabular-nums text-fg text-base">
              {data[k] as number}
            </div>
          </li>
        ))}
      </ul>
      {data.is_sufficient_data ? (
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
      ) : (
        <p className="text-3xs text-fg-subtle">
          Conversion rates require ≥5 applications (currently {data.total}).
          Raw counts above are live from{' '}
          <code className="font-mono">v_application_funnel</code>.
        </p>
      )}
    </div>
  )
}

// ── Partitioned card: always show the real partitions ──────────────────
function PartitionedCard({
  data,
  dimensionLabel,
  viewName,
}: {
  data: PartitionedFunnelResponse
  dimensionLabel: string
  viewName: string
}) {
  if (data.partitions.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle">
        No rows yet in <code className="font-mono">{viewName}</code>.
      </p>
    )
  }

  return (
    <div className="space-y-1.5">
      <table className="w-full text-2xs">
        <thead className="text-fg-subtle">
          <tr>
            <th className="text-left font-normal">{dimensionLabel}</th>
            <th className="text-right font-normal">Applied</th>
            <th className="text-right font-normal">Resp</th>
            <th className="text-right font-normal">Intv</th>
            <th className="text-right font-normal">Offer</th>
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
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-3xs text-fg-subtle">
        {data.is_sufficient_data
          ? `Conversion rates available across ${data.partitions.length} ${dimensionLabel.toLowerCase()} value(s).`
          : `${data.partitions.length} ${dimensionLabel.toLowerCase()} value(s) live from `}
        {!data.is_sufficient_data && (
          <code className="font-mono">{viewName}</code>
        )}
      </p>
    </div>
  )
}

function RejectionsCard({ data }: { data: RejectionPatternsResponse }) {
  if (data.patterns.length === 0) {
    return (
      <div className="space-y-1">
        <p className="text-2xs text-fg-subtle">
          No clusters with ≥1.5× lift detected yet.
        </p>
        <p className="text-3xs text-fg-subtle">
          {data.message ||
            'Mining v_rejection_signals — needs ≥5 rejections in one bucket.'}
        </p>
      </div>
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
  // BUG-013 hardening: hide rows that are ALL zeros — those are phantom
  // company_personas leftovers from the pre-is_phantom era. Real spend
  // rows show non-zero totals; pure-zero rows are noise.
  const meaningful = data.rows.filter(
    (r) =>
      r.total_cost_usd > 0 ||
      r.total_calls > 0 ||
      r.interviews > 0 ||
      r.offers > 0,
  )

  if (meaningful.length === 0) {
    return (
      <div className="space-y-1">
        <p className="text-2xs text-fg-subtle">
          No LLM spend recorded against a real company yet.
        </p>
        <p className="text-3xs text-fg-subtle">
          {data.rows.length > 0
            ? `Filtered ${data.rows.length} all-zero phantom row(s).`
            : 'v_cost_per_outcome is empty for this user.'}
        </p>
      </div>
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
        {meaningful.map((r) => (
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

// ── Section wrapper ────────────────────────────────────────────────────
function Section({
  title,
  source,
  children,
}: {
  title: string
  source: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2 rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <code className="text-3xs text-fg-subtle font-mono">{source}</code>
      </div>
      {children}
    </section>
  )
}

// ── Main ───────────────────────────────────────────────────────────────
export async function Analytics() {
  const data = await loadAll()
  const loadedAt = new Date()

  return (
    <div className="space-y-4">
      <FreshnessBar at={loadedAt} />

      <Section title="Top-level funnel" source="v_application_funnel">
        {data.funnel.ok ? (
          <FunnelCard data={data.funnel.value as FunnelResponse} />
        ) : (
          <p className="text-2xs text-rose-300">
            Live data unavailable: {data.funnel.ok ? '' : data.funnel.error}
          </p>
        )}
      </Section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Section
          title="By grade"
          source="v_application_funnel_by_grade"
        >
          {data.byGrade.ok ? (
            <PartitionedCard
              data={data.byGrade.value as PartitionedFunnelResponse}
              dimensionLabel="Grade"
              viewName="v_application_funnel_by_grade"
            />
          ) : (
            <p className="text-2xs text-rose-300">
              {data.byGrade.ok ? '' : data.byGrade.error}
            </p>
          )}
        </Section>
        <Section title="By archetype" source="v_application_funnel_by_archetype">
          {data.byArch.ok ? (
            <PartitionedCard
              data={data.byArch.value as PartitionedFunnelResponse}
              dimensionLabel="Archetype"
              viewName="v_application_funnel_by_archetype"
            />
          ) : (
            <p className="text-2xs text-rose-300">
              {data.byArch.ok ? '' : data.byArch.error}
            </p>
          )}
        </Section>
        <Section title="By company size" source="v_application_funnel_by_company_size">
          {data.bySize.ok ? (
            <PartitionedCard
              data={data.bySize.value as PartitionedFunnelResponse}
              dimensionLabel="Size"
              viewName="v_application_funnel_by_company_size"
            />
          ) : (
            <p className="text-2xs text-rose-300">
              {data.bySize.ok ? '' : data.bySize.error}
            </p>
          )}
        </Section>
      </div>

      <Section
        title="Rejection clusters (≥1.5× lift)"
        source="v_rejection_signals"
      >
        {data.rejPatterns.ok ? (
          <RejectionsCard data={data.rejPatterns.value as RejectionPatternsResponse} />
        ) : (
          <p className="text-2xs text-rose-300">
            {data.rejPatterns.ok ? '' : data.rejPatterns.error}
          </p>
        )}
      </Section>

      <Section
        title="Cost efficiency by company"
        source="v_cost_per_outcome"
      >
        {data.costEff.ok ? (
          <CostEfficiencyCard data={data.costEff.value as CostEfficiencyResponse} />
        ) : (
          <p className="text-2xs text-rose-300">
            {data.costEff.ok ? '' : data.costEff.error}
          </p>
        )}
      </Section>
    </div>
  )
}
