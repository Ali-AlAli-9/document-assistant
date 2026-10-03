import React, { useEffect, useRef, useState } from 'react'
import { IconDocument } from './Icons'

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

function UserAvatar() {
  return (
    <div className="message-avatar">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    </div>
  )
}

function AssistantAvatar() {
  return (
    <div className="message-avatar">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2a10 10 0 1 0 10 10h-10V2z" />
        <path d="M12 12V2a10 10 0 0 1 10 10h-10z" />
        <path d="M12 12H2a10 10 0 0 0 10 10V12z" />
      </svg>
    </div>
  )
}

function SourceItem({ source, index }) {
  const [expanded, setExpanded] = useState(false)
  const excerptRef = useRef(null)
  const [overflowing, setOverflowing] = useState(false)

  const title =
    source.title ||
    (source.document_id ? `المستند #${source.document_id}` : `مصدر ${index + 1}`)
  const percent = Math.round((source.score || 0) * 100)

  useEffect(() => {
    const el = excerptRef.current
    if (el) setOverflowing(el.scrollHeight > el.clientHeight + 1)
  }, [source.content])

  return (
    <div className="source-item">
      <div className="source-item-header">
        <span className="source-item-title"><span className="icon-inline"><IconDocument size={13} /></span>{title}</span>
        <span className="source-item-score" dir="ltr">{percent}%</span>
      </div>
      <p
        ref={excerptRef}
        dir="rtl"
        className={`source-excerpt${expanded ? ' expanded' : ''}`}
      >
        {source.content}
      </p>
      {overflowing && (
        <button
          type="button"
          className="source-expand-btn"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'عرض أقل ▲' : 'عرض المزيد ▼'}
        </button>
      )}
    </div>
  )
}

export default function ChatMessage({ role, content, sources, error }) {
  const isUser = role === 'user'
  return (
    <div className={`message ${isUser ? 'message-user' : 'message-assistant'}${error ? ' message-failed' : ''}`}>
      {isUser ? <UserAvatar /> : <AssistantAvatar />}
      <div className="message-content">
        <div className="message-role">{isUser ? 'أنت' : 'المساعد'}</div>
        {content ? (
          <div
            className="message-text"
            dangerouslySetInnerHTML={{ __html: escapeHtml(content).replace(/\n/g, '<br>') }}
          />
        ) : error ? (
          <div className="message-error">{error}</div>
        ) : null}
        {error && content && <div className="message-error-note">{error}</div>}
        {sources && sources.length > 0 && (
          <details className="sources-toggle">
            <summary>المصادر ({sources.length})</summary>
            <div className="sources-list">
              {sources.map((s, i) => (
                <SourceItem key={`${s.document_id ?? i}-${i}`} source={s} index={i} />
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  )
}
