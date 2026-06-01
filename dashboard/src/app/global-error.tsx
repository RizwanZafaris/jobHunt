/**
 * app/global-error.tsx — last-resort boundary for errors thrown in the
 * ROOT layout itself (where the normal app/error.tsx can't render because
 * the layout is what failed). Must render its own <html>/<body>.
 *
 * Intentionally dependency-free (no AppShell / theme tokens) so it works
 * even if the failure is in the layout/providers. Plain inline styles keep
 * it bulletproof.
 */
'use client'

import { useEffect } from 'react'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error('Global error boundary caught:', error)
  }, [error])

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          background: '#0a0a0a',
          color: '#fafafa',
        }}
      >
        <div style={{ maxWidth: 420, padding: 24, textAlign: 'center' }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: '0 0 8px' }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: 14, color: '#a3a3a3', margin: '0 0 20px', lineHeight: 1.5 }}>
            The app hit an unexpected error while loading. Try reloading.
          </p>
          <button
            onClick={() => reset()}
            style={{
              cursor: 'pointer',
              border: 'none',
              borderRadius: 6,
              padding: '8px 16px',
              fontSize: 14,
              fontWeight: 500,
              background: '#fafafa',
              color: '#0a0a0a',
            }}
          >
            Reload
          </button>
          {error?.digest && (
            <p style={{ fontSize: 11, color: '#666', marginTop: 16 }}>
              Reference: {error.digest}
            </p>
          )}
        </div>
      </body>
    </html>
  )
}
