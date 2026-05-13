/**
 * /insights/analytics — now a redirect.
 *
 * The Analytics surface was lifted into the /insights TabStrip as a 4th
 * tab so it's discoverable from the top-level nav (the user was navigating
 * to /insights, not seeing an analytics link, and assumed the page was
 * missing). This route stays for back-compat with any external links or
 * bookmarks and 308s to the canonical tab URL.
 */
import { permanentRedirect } from 'next/navigation'

export default function AnalyticsRedirect() {
  permanentRedirect('/insights?tab=analytics')
}
