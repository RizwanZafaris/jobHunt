/**
 * loading.tsx — /applications skeleton (wide). Row-list shape.
 */
import { AppShell } from '@/components/layout/AppShell'
import { Card } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'

export default function ApplicationsLoading() {
  return (
    <AppShell wide>
      <div className="space-y-2 pb-2">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-8 w-52" />
      </div>
      <Card padding="md">
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton className="h-9 w-9 rounded-md shrink-0" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-3 w-1/4" />
              </div>
              <Skeleton className="h-6 w-16 rounded-full shrink-0" />
            </div>
          ))}
        </div>
      </Card>
    </AppShell>
  )
}
