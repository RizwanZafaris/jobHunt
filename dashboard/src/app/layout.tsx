import type { Metadata, Viewport } from 'next'
import { cookies } from 'next/headers'
import './globals.css'
import { ThemeScript } from '@/components/ui/ThemeScript'

export const metadata: Metadata = {
  title: "Job Hunt — Rizwan's Dashboard",
  description: 'Multi-agent job hunt pipeline · evaluation, applications, persona learning, cost telemetry.',
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fafaf9' },
    { media: '(prefers-color-scheme: dark)',  color: '#09090b' },
  ],
}

export default async function RootLayout(
  {
    children,
  }: {
    children: React.ReactNode
  }
) {
  // Read theme cookie server-side so SSR ships HTML with the correct
  // `data-theme` attribute. No flash, no client-side rewrite for users
  // who have already toggled.
  const cookieTheme = (await cookies()).get('theme')?.value
  const theme = cookieTheme === 'light' || cookieTheme === 'dark' ? cookieTheme : undefined

  return (
    <html
      lang="en"
      suppressHydrationWarning
      data-theme={theme}
    >
      <head>
        {/* Fallback for users without a cookie yet — sets data-theme from
            prefers-color-scheme before paint. Skipped (noop) if the
            server already injected data-theme. */}
        <ThemeScript hasServerTheme={!!theme} />
      </head>
      <body className="min-h-screen bg-bg text-fg font-sans antialiased">
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  )
}
