'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import type { ProfileMaster } from '@/lib/profile-api'
import { updateProfile } from '@/lib/profile-api'
import EditableField from './EditableField'
import EditableTagList from './EditableTagList'

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
    <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-8 space-y-3">
      <EditableField
        value={m.name}
        onSave={(v) => patch({ name: v })}
        className="text-3xl font-bold text-white tracking-tight"
        inputClassName="text-3xl font-bold"
        label="Name"
      />
      <EditableField
        value={m.headline}
        onSave={(v) => patch({ headline: v })}
        className="text-base text-blue-400"
        inputClassName="text-base"
        label="Headline"
        placeholder="Add a headline..."
      />
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-gray-400 mt-3">
        <span>📍 <EditableField value={m.location} onSave={(v) => patch({ location: v })} className="inline" /></span>
        <span>✉ <EditableField value={m.email} onSave={(v) => patch({ email: v })} className="inline" /></span>
        <span>🔗 <EditableField value={m.linkedin_url} onSave={(v) => patch({ linkedin_url: v })} className="inline" /></span>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Summary</div>
        <EditableField
          value={m.summary}
          onSave={(v) => patch({ summary: v })}
          multiline
          className="text-sm text-gray-300 leading-relaxed"
          inputClassName="text-sm leading-relaxed"
          placeholder="Add executive summary..."
        />
      </div>
      <div className="pt-3 border-t border-gray-800">
        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Phones</div>
        <EditableTagList
          values={m.phones || []}
          onChange={(v) => patch({ phones: v })}
          badgeClassName="bg-gray-800 border border-gray-700 text-gray-300"
          addPlaceholder="+971..."
        />
      </div>
      <div className="pt-3 border-t border-gray-800">
        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Core Competencies</div>
        <EditableTagList
          values={m.core_competencies || []}
          onChange={(v) => patch({ core_competencies: v })}
          badgeClassName="bg-gray-800 border border-gray-700 text-gray-300"
          addPlaceholder="e.g. Product Strategy"
        />
      </div>
      <div className="pt-3 border-t border-gray-800">
        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Technical Knowledge</div>
        <EditableTagList
          values={m.technical_knowledge || []}
          onChange={(v) => patch({ technical_knowledge: v })}
          badgeClassName="bg-blue-900/40 border border-blue-800 text-blue-300"
          addPlaceholder="e.g. ISO 20022"
        />
      </div>
    </section>
  )
}
