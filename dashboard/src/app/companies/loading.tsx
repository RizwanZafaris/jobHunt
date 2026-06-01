/**
 * loading.tsx — /companies (Targets) skeleton. Card-grid shape.
 */
import { AppShell } from '@/components/layout/AppShell'
import { Card } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'

export default function CompaniesLoading() {
  return (
    <AppShell>
      <div className="space-y-2 pb-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-8 w-44" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {Array.from({ length: 9 }).map((_, i) => (
          <Card key={i} padding="md">
            <div className="space-y-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-5 w-20 rounded-full" />
            </div>
          </Card>
        ))}
      </div>
    </AppShell>
  )
}
