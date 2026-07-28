import React from 'react'

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

export default function ChatMessage({ role, content, sources }) {
  const isUser = role === 'user'
  return (
    <div className={`message ${isUser ? 'message-user' : 'message-assistant'}`}>
      {isUser ? <UserAvatar /> : <AssistantAvatar />}
      <div className="message-content">
        <div className="message-role">{isUser ? 'أنت' : 'المساعد'}</div>
        <div
          className="message-text"
          dangerouslySetInnerHTML={{ __html: escapeHtml(content).replace(/\n/g, '<br>') }}
        />
        {sources && sources.length > 0 && (
          <details className="sources-toggle">
            <summary>المصادر ({sources.length})</summary>
            <div className="sources-list">
              {sources.map((s, i) => (
                <div key={`${s.document_id ?? 'x'}-${i}`} className="source-item">
                  <div className="source-score">
                    {'★'.repeat(Math.round(s.score * 5))}
                    <span>{(s.score * 100).toFixed(0)}%</span>
                  </div>
                  <p className="source-text">{s.content}</p>
                  {s.document_id && (
                    <span className="source-doc">المستند #{s.document_id}</span>
                  )}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  )
}
