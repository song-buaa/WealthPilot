import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import ActionDraftCard from '../../src/components/ActionDraftCard'
import type { ActionDraftResponse } from '../../src/lib/api'
export default function Harness() {
  const [draft, setDraft] = useState<ActionDraftResponse | null>(null)
  const [open, setOpen] = useState(true)
  Object.assign(window, { setDraft, setOpen })
  return <ActionDraftCard open={open} draft={draft} onClose={() => setOpen(false)} onConfirmed={() => {}} />
}
createRoot(document.getElementById('root')!).render(<Harness />)
