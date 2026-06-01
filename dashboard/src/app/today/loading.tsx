/**
 * loading.tsx — /today skeleton. Mirrors the section-per-kind layout so
 * the user sees the page's shape instantly while sections fetch.
 */
import { AppShell } from '@/components/layout/AppShell'
import { Card } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'

export default function TodayLoading() {
  return (
    <AppShell>
      <div className="space-y-2 pb-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-8 w-48" />
      </div>
      <div className="space-y-5">
        {Array.from({ length: 3 }).map((_, s) => (
          <section key={s} className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {Array.from({ length: 2 }).map((_, i) => (
                <Card key={i} padding="md">
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                    <Skeleton className="h-3 w-2/3" />
                  </div>
                </Card>
              ))}
            </div>
          </section>
        ))}
      </div>
    </AppShell>
  )
}
