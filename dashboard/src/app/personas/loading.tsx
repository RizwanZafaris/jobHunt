/**
 * loading.tsx — /personas skeleton. Quality-table shape.
 */
import { AppShell } from '@/components/layout/AppShell'
import { Card } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'

export default function PersonasLoading() {
  return (
    <AppShell>
      <div className="space-y-2 pb-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-8 w-40" />
      </div>
      <Card padding="md">
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-3 w-1/5" />
              </div>
              <Skeleton className="h-5 w-14 rounded-full shrink-0" />
              <Skeleton className="h-7 w-20 rounded-md shrink-0" />
            </div>
          ))}
        </div>
      </Card>
    </AppShell>
  )
}
