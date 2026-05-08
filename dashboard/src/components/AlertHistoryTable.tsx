'use client'

import { type AlertHistoryEntry } from '@/lib/profile-api'

interface Props {
  alerts: AlertHistoryEntry[]
  warning?: string
}

interface ParsedAlert {
  kind: string | null
  fired: boolean | null
  channel: string | null
  body: string
  parseOk: boolean
}

const HEADER_RE = /^\[cost-alerter:(\w+)\]\s+fired=(\w+)\s+channel=(\S+)/

function parseAlert(content: string): ParsedAlert {
  if (!content) {
    return { kind: null, fired: null, channel: null, body: '', parseOk: false }
  }
  const m = content.match(HEADER_RE)
  if (!m) {
    return { kind: null, fired: null, channel: null, body: content, parseOk: false }
  }
  const [, kind, firedRaw, channel] = m
  const fired = firedRaw.toLowerCase() === 'true'
  const split = content.indexOf('\n\n')
  const body = split >= 0 ? content.slice(split + 2) : ''
  return { kind, fired, channel, body, parseOk: true }
}

function relativeTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = Date.now()
  const diffSec = Math.floor((now - d.getTime()) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function rowBorderClass(kind: string | null, fired: boolean | null): string {
  if (kind === 'daily' && fired === true) {
    return 'border-l-2 border-amber-700/40'
  }
  if (kind === 'weekly_digest') {
    return 'border-l-2 border-blue-700/40'
  }
  return 'border-l-2 border-transparent'
}

export default function AlertHistoryTable({ alerts, warning }: Props) {
  const isEmpty = alerts.length === 0

  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-base font-semibold text-white">Recent Alerts</h2>
        <p className="text-[11px] text-gray-500 mt-0.5">
          Last 10 cost-alerter entries from{' '}
          <code className="text-gray-400">boss_audit_log</code>
        </p>
      </div>

      {warning && (
        <div className="px-4 py-3 text-[11px] text-amber-400 bg-amber-900/10 border-b border-amber-900/30">
          ⚠ {warning}
        </div>
      )}

      {isEmpty ? (
        <div className="m-4 border border-dashed border-gray-700 rounded-lg p-8 text-center text-sm text-gray-500 italic">
          No alerts fired yet — system has been quiet
        </div>
      ) : (
        <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-950/80 text-gray-400 uppercase text-[10px] sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">When</th>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">Kind</th>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">Channel</th>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">Status</th>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">Preview</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {alerts.map((a) => {
                const parsed = parseAlert(a.digest_content || '')
                const preview = parsed.parseOk
                  ? parsed.body.slice(0, 200)
                  : (a.digest_content || '').slice(0, 200)
                const previewHasMore = parsed.parseOk
                  ? parsed.body.length > 200
                  : (a.digest_content || '').length > 200
                return (
                  <tr
                    key={a.id}
                    className={`hover:bg-gray-800/30 ${rowBorderClass(parsed.kind, parsed.fired)}`}
                  >
                    <td
                      className="px-3 py-2 text-gray-400 whitespace-nowrap align-top"
                      title={a.created_at}
                    >
                      {relativeTime(a.created_at)}
                    </td>
                    <td className="px-3 py-2 align-top">
                      {parsed.kind ? (
                        <code className="text-blue-400 font-mono text-[11px]">
                          {parsed.kind}
                        </code>
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top">
                      {parsed.channel && parsed.channel !== 'None' ? (
                        <span className="text-[10px] bg-gray-800 border border-gray-700 px-1.5 rounded text-gray-300">
                          {parsed.channel}
                        </span>
                      ) : (
                        <span className="text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top whitespace-nowrap">
                      <span
                        className={`text-[10px] border px-1.5 py-0.5 rounded uppercase mr-1 ${
                          parsed.fired === true
                            ? 'bg-amber-900/40 border-amber-800 text-amber-300'
                            : parsed.fired === false
                            ? 'bg-gray-800 border-gray-700 text-gray-400'
                            : 'bg-gray-800 border-gray-700 text-gray-500'
                        }`}
                      >
                        fired:{' '}
                        {parsed.fired === true
                          ? 'Y'
                          : parsed.fired === false
                          ? 'N'
                          : '—'}
                      </span>
                      <span
                        className={`text-[10px] border px-1.5 py-0.5 rounded uppercase ${
                          a.digest_sent
                            ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
                            : 'bg-gray-800 border-gray-700 text-gray-400'
                        }`}
                      >
                        sent: {a.digest_sent ? 'Y' : 'N'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-gray-300 align-top">
                      <details className="group">
                        <summary className="cursor-pointer list-none">
                          <span className="text-gray-400">
                            {preview}
                            {previewHasMore && '…'}
                          </span>
                          <span className="ml-2 text-[10px] text-blue-400 group-open:hidden">
                            show full
                          </span>
                          <span className="ml-2 text-[10px] text-blue-400 hidden group-open:inline">
                            hide
                          </span>
                        </summary>
                        <pre className="mt-2 text-[11px] text-gray-300 whitespace-pre-wrap font-sans leading-relaxed bg-gray-950/60 border border-gray-800 rounded p-2 max-h-64 overflow-y-auto">
                          {a.digest_content || '(empty)'}
                        </pre>
                      </details>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
