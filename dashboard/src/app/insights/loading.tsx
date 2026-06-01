/**
 * loading.tsx — /insights skeleton (wide). KPI strip + chart blocks.
 */
import { AppShell } from '@/components/layout/AppShell'
import { Card } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'

export default function InsightsLoading() {
  return (
    <AppShell wide>
      <div className="space-y-2 pb-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-8 w-44" />
      </div>
      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i} padding="md">
            <div className="space-y-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-7 w-16" />
            </div>
          </Card>
        ))}
      </div>
      {/* Chart blocks */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {Array.from({ length: 2 }).map((_, i) => (
          <Card key={i} padding="md">
            <Skeleton className="h-4 w-32 mb-3" />
            <Skeleton className="h-48 w-full rounded-md" />
          </Card>
        ))}
      </div>
    </AppShell>
  )
}
