/**
 * loading.tsx — workspace skeleton.
 *
 * Renders the layout chrome the moment the route is hit. The Server
 * Component's `fetchWorkspace` is fast (one denormalised JSON), but the
 * skeleton keeps perceived speed steady and signals which page they
 * landed on.
 */
import { AppShell } from '@/components/layout/AppShell'
import { Card } from '@/components/ui/Card'
import { Skeleton } from '@/components/ui/Skeleton'

export default function WorkspaceLoading() {
  return (
    <AppShell wide>
      {/* Header strip */}
      <div className="flex items-start gap-4 pb-3">
        <Skeleton className="w-12 h-12 rounded-md shrink-0" />
        <div className="min-w-0 flex-1 space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-3 w-40" />
        </div>
        <Skeleton className="h-9 w-36 rounded-md shrink-0" />
      </div>

      {/* Tab strip */}
      <div className="flex items-center gap-2 border-b border-border pb-1">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-7 w-24 rounded-md" />
        ))}
      </div>

      {/* Body — two column for the resume / role brief feel */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-3">
          <Card padding="md">
            <div className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-5/6" />
              <Skeleton className="h-3 w-4/5" />
              <Skeleton className="h-3 w-3/4" />
            </div>
          </Card>
          <Card padding="md">
            <div className="space-y-2">
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-11/12" />
              <Skeleton className="h-3 w-3/4" />
            </div>
          </Card>
        </div>
        <div className="space-y-3">
          <Card padding="md">
            <div className="space-y-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-3/4" />
              <Skeleton className="h-3 w-2/3" />
            </div>
          </Card>
          <Card padding="md">
            <div className="space-y-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-5/6" />
            </div>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}
