'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import type { ProfileExperience } from '@/lib/profile-api'
import { updateExperience } from '@/lib/profile-api'
import EditableField from './EditableField'

interface Props {
  initial: ProfileExperience
}

export default function EditableExperience({ initial }: Props) {
  const router = useRouter()
  const [e, setE] = useState<ProfileExperience>(initial)

  async function patch(updates: Partial<ProfileExperience>) {
    const result = await updateExperience(e.id, updates)
    if (result?.row) {
      setE(result.row)
    } else {
      setE({ ...e, ...updates })
    }
    router.refresh()
  }

  async function updateHighlight(idx: number, next: string) {
    const arr = [...(e.highlights || [])]
    arr[idx] = next
    await patch({ highlights: arr })
  }

  async function deleteHighlight(idx: number) {
    const arr = [...(e.highlights || [])]
    arr.splice(idx, 1)
    await patch({ highlights: arr })
  }

  async function addHighlight() {
    const arr = [...(e.highlights || []), 'New highlight (click to edit)']
    await patch({ highlights: arr })
  }

  async function updateGroupBullet(gIdx: number, bIdx: number, next: string) {
    const groups = e.groups ? JSON.parse(JSON.stringify(e.groups)) : []
    groups[gIdx].bullets[bIdx] = next
    await patch({ groups })
  }

  async function deleteGroupBullet(gIdx: number, bIdx: number) {
    const groups = e.groups ? JSON.parse(JSON.stringify(e.groups)) : []
    groups[gIdx].bullets.splice(bIdx, 1)
    await patch({ groups })
  }

  async function updateGroupLabel(gIdx: number, next: string) {
    const groups = e.groups ? JSON.parse(JSON.stringify(e.groups)) : []
    groups[gIdx].label = next
    await patch({ groups })
  }

  return (
    <div className="border-l-2 border-border-strong pl-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-fg flex-1">
          <EditableField value={e.title} onSave={(v) => patch({ title: v })} />
        </h3>
        <span className="text-xs text-fg-subtle whitespace-nowrap">
          <EditableField value={e.dates} onSave={(v) => patch({ dates: v })} />
        </span>
      </div>
      <p className="text-xs text-info mt-1 flex flex-wrap gap-x-1">
        <EditableField value={e.company} onSave={(v) => patch({ company: v })} />
        {e.location && (
          <span className="text-fg-subtle">
            · <EditableField value={e.location} onSave={(v) => patch({ location: v })} />
          </span>
        )}
        {e.scope && (
          <span className="text-fg-subtle">
            · <EditableField value={e.scope} onSave={(v) => patch({ scope: v })} />
          </span>
        )}
      </p>
      {(e.summary || true) && (
        <div className="text-xs text-fg-muted mt-2 leading-relaxed">
          <EditableField
            value={e.summary || ''}
            onSave={(v) => patch({ summary: v })}
            multiline
            placeholder="Add summary for this role..."
          />
        </div>
      )}

      {/* Flat highlights */}
      {(e.highlights?.length > 0 || true) && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-2xs uppercase tracking-wider text-fg-subtle">Highlights</span>
            <button
              onClick={addHighlight}
              className="text-2xs bg-info-bg hover:bg-info-bg text-fg px-2 py-0.5 rounded"
            >
              + add
            </button>
          </div>
          <ul className="text-xs text-fg-muted space-y-1.5">
            {(e.highlights || []).map((h, i) => (
              <li key={i} className="flex gap-2 group">
                <span className="text-fg-subtle">•</span>
                <span className="flex-1 leading-relaxed">
                  <EditableField value={h} onSave={(v) => updateHighlight(i, v)} multiline />
                </span>
                <button
                  onClick={() => deleteHighlight(i)}
                  className="opacity-0 group-hover:opacity-100 text-2xs text-danger hover:text-danger"
                  title="Delete bullet"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Grouped bullets */}
      {e.groups?.length > 0 && (
        <div className="mt-3 space-y-3">
          {e.groups.map((g, gi) => (
            <div key={gi}>
              <h4 className="text-xs font-semibold text-fg mb-1">
                <EditableField value={g.label} onSave={(v) => updateGroupLabel(gi, v)} />
              </h4>
              <ul className="text-xs text-fg-muted space-y-1.5 ml-2">
                {g.bullets.map((b, bi) => (
                  <li key={bi} className="flex gap-2 group">
                    <span className="text-fg-subtle">•</span>
                    <span className="flex-1 leading-relaxed">
                      <EditableField
                        value={b}
                        onSave={(v) => updateGroupBullet(gi, bi, v)}
                        multiline
                      />
                    </span>
                    <button
                      onClick={() => deleteGroupBullet(gi, bi)}
                      className="opacity-0 group-hover:opacity-100 text-2xs text-danger hover:text-danger"
                      title="Delete bullet"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
