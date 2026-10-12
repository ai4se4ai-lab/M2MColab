import { useState } from 'react'
import { Check, Copy as CopyIcon } from '@phosphor-icons/react'

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="copy-btn"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setDone(true)
            setTimeout(() => setDone(false), 1500)
          },
          () => setDone(false),
        )
      }}
    >
      {done ? <Check size={13} /> : <CopyIcon size={13} />} {done ? 'Copied' : label}
    </button>
  )
}

export function Snippet({ title, text }: { title: string; text: string }) {
  return (
    <div className="snippet">
      <div className="code-head">
        <span>{title}</span>
        <CopyButton text={text} />
      </div>
      <pre className="code">{text}</pre>
    </div>
  )
}
