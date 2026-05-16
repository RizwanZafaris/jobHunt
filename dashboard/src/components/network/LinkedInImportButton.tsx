/**
 * LinkedInImportButton — file-input + submit for /network/import/linkedin-csv.
 *
 * V1: stubs the upload since the FastAPI router isn't wired into
 * api/server.py yet (see api/NETWORK.md "How to wire the router"). When
 * a file is selected we resolve to the mock summary so the surface
 * exercises end-to-end.
 *
 * The native <input type="file" accept=".csv"> is the entire
 * accessibility / UX story — no fancy drag-and-drop in V1; the LinkedIn
 * CSV is a one-time upload, not a recurring task.
 */
'use client'

import { ChangeEvent, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import type { ImportSummary } from '@/lib/types/network'

const ACCEPT = '.csv,text/csv'

export interface LinkedInImportButtonProps {
  onSummary: (summary: ImportSummary) => void
}

export function LinkedInImportButton({ onSummary }: LinkedInImportButtonProps) {
  const ref = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)

  function open() {
    ref.current?.click()
  }

  async function handleChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true)
    setErrMsg(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch('/api/proxy/network/import/linkedin-csv', {
        method: 'POST',
        body: fd,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Upload failed (${res.status})`)
      }
      const summary: ImportSummary = await res.json()
      onSummary(summary)
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setBusy(false)
      // Allow re-uploading the same file.
      if (ref.current) ref.current.value = ''
    }
  }

  return (
    <div className="flex flex-col items-stretch sm:items-end gap-1">
      <input
        ref={ref}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        onChange={handleChange}
        aria-label="LinkedIn connections CSV"
      />
      <Button
        type="button"
        variant="secondary"
        size="md"
        onClick={open}
        loading={busy}
      >
        <Icon name="download" size={14} />
        Import LinkedIn CSV
      </Button>
      {errMsg && (
        <p role="alert" className="text-2xs text-danger">
          {errMsg}
        </p>
      )}
    </div>
  )
}

export default LinkedInImportButton
