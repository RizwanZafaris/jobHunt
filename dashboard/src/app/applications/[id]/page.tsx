/**
 * /applications/[id] — landing redirect.
 *
 * The application detail surface is split across four sub-routes
 * (workspace, assist, interview-studio, offer). Hitting the bare
 * /applications/{id} URL has historically returned 404, which is
 * surprising — links from /actions/today and the applications list
 * land on workspace by default, but a paste-in-URL hit was broken.
 *
 * This page redirects to the workspace, preserving the [id] segment.
 * Use 307 (preserves method) over 308 so a future POST-as-redirect
 * doesn't get re-issued as POST against the workspace sub-route.
 */
import { redirect } from 'next/navigation'

interface PageProps {
  params: Promise<{ id: string }>
}

export default async function ApplicationIndexPage({ params }: PageProps) {
  const { id } = await params
  redirect(`/applications/${id}/workspace`)
}
