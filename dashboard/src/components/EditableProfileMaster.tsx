'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import type { ProfileMaster } from '@/lib/profile-api'
import { updateProfile } from '@/lib/profile-api'
import EditableField from './EditableField'
import EditableTagList from './EditableTagList'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'

interface Props {
  initial: ProfileMaster
}

export default function EditableProfileMaster({ initial }: Props) {
  const router = useRouter()
  const [m, setM] = useState<ProfileMaster>(initial)

  async function patch(updates: Partial<ProfileMaster>) {
    const result = await updateProfile(updates)
    if (result?.row) {
      setM(result.row)
    } else {
      setM({ ...m, ...updates })
    }
    router.refresh()
  }

  return (
    <Card padding="lg">
      <div className="space-y-3">
        <EditableField
          value={m.name}
          onSave={(v) => patch({ name: v })}
          className="text-3xl font-display font-normal text-fg tracking-tight"
          inputClassName="text-3xl font-display"
          srLabel="Name"
        />
        <EditableField
          value={m.headline}
          onSave={(v) => patch({ headline: v })}
          className="text-base text-fg-muted"
          inputClassName="text-base"
          srLabel="Headline"
          placeholder="Add a headline…"
        />
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-fg-muted">
          <span className="inline-flex items-center gap-1.5">
            <Icon name="building" size={12} className="text-fg-subtle" />
            <EditableField
              value={m.location}
              onSave={(v) => patch({ location: v })}
              className="inline"
              srLabel="Location"
              placeholder="Location"
            />
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Icon name="mail" size={12} className="text-fg-subtle" />
            <EditableField
              value={m.email}
              onSave={(v) => patch({ email: v })}
              className="inline"
              srLabel="Email"
              placeholder="Email"
            />
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Icon name="link" size={12} className="text-fg-subtle" />
            <EditableField
              value={m.linkedin_url}
              onSave={(v) => patch({ linkedin_url: v })}
              className="inline"
              srLabel="LinkedIn URL"
              placeholder="LinkedIn URL"
            />
          </span>
        </div>
        <div className="pt-3 border-t border-border">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-1.5 font-semibold">Summary</div>
          <EditableField
            value={m.summary}
            onSave={(v) => patch({ summary: v })}
            multiline
            className="text-sm text-fg-muted leading-relaxed"
            inputClassName="text-sm leading-relaxed"
            placeholder="Add executive summary…"
            srLabel="Summary"
          />
        </div>
        <div className="pt-3 border-t border-border">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-2 font-semibold">Phones</div>
          <EditableTagList
            values={m.phones || []}
            onChange={(v) => patch({ phones: v })}
            badgeClassName="bg-surface-raised border border-border-strong text-fg-muted"
            addPlaceholder="+971…"
          />
        </div>
        <div className="pt-3 border-t border-border">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-2 font-semibold">Core competencies</div>
          <EditableTagList
            values={m.core_competencies || []}
            onChange={(v) => patch({ core_competencies: v })}
            badgeClassName="bg-surface-raised border border-border-strong text-fg-muted"
            addPlaceholder="e.g. Product Strategy"
          />
        </div>
        <div className="pt-3 border-t border-border">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle mb-2 font-semibold">Technical knowledge</div>
          <EditableTagList
            values={m.technical_knowledge || []}
            onChange={(v) => patch({ technical_knowledge: v })}
            badgeClassName="bg-info-bg border border-info-border text-info"
            addPlaceholder="e.g. ISO 20022"
          />
        </div>
      </div>
    </Card>
  )
}
