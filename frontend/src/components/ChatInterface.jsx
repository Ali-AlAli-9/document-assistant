import React, { useState, useRef, useEffect } from 'react'
import { api } from '../api/client'
import { friendlyError } from '../api/errors'
import ChatMessage from './ChatMessage'

export default function ChatInterface({ embedderReady }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [conversationId, setConversationId] = useState(null)
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function handleSubmit(e) {
    e.preventDefault()
    const question = input.trim()
    if (!question || streaming) return

    setError('')
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setStreaming(true)

    const assistantMsg = { role: 'assistant', content: '', sources: [] }
    setMessages((prev) => [...prev, assistantMsg])

    try {
      let sourcesCollected = []
      for await (const data of api.askQuestionStream(question, conversationId)) {
        if (data.conversation_id && !conversationId) {
          setConversationId(data.conversation_id)
        }
        if (data.sources) {
          sourcesCollected = data.sources
        }
        if (data.done) break
        // حدث خطأ طرفي (لا يوجد `done` بعده في العقد الخلفي) — نوقف البث فورًا
        if (data.error) {
          const errText = friendlyError(
            typeof data.error === 'string' ? { detail: data.error } : null,
            'حدث خطأ أثناء توليد الرد — أعد المحاولة.',
          )
          setError(errText)
          setMessages((prev) => {
            const updated = [...prev]
            const last = updated[updated.length - 1]
            if (last && last.role === 'assistant') {
              updated[updated.length - 1] = { ...last, error: errText }
            }
            return updated
          })
          return
        }
        if (data.chunk) {
          setMessages((prev) => {
            const updated = [...prev]
            const last = updated[updated.length - 1]
            if (last && last.role === 'assistant') {
              updated[updated.length - 1] = { ...last, content: last.content + data.chunk }
            }
            return updated
          })
        }
      }
      // لا نُرفق المصادر إلا بعد اكتمال البث بنجاح (بدون خطأ)
      if (sourcesCollected.length > 0) {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last && last.role === 'assistant' && !last.error) {
            updated[updated.length - 1] = { ...last, sources: sourcesCollected }
          }
          return updated
        })
      }
    } catch (err) {
      setError(friendlyError(err, 'تعذّر الاتصال بالخادم — تأكد من تشغيل الخادم ثم أعد المحاولة.'))
    } finally {
      setStreaming(false)
    }
  }

  function handleNewChat() {
    setMessages([])
    setConversationId(null)
    setError('')
  }

  return (
    <div className="card chat-card">
      <div className="chat-header">
        <h2>المحادثة</h2>
        {messages.length > 0 && (
          <button className="btn-new-chat" onClick={handleNewChat} title="محادثة جديدة">
            +
          </button>
        )}
      </div>
      {error && <div className="error-message">{error}</div>}
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty-state">
            {!embedderReady
              ? 'جاري تحميل نموذج التحليل... يرجى الانتظار'
              : 'اطرح سؤالاً حول المستندات المرفوعة'
            }
          </div>
        )}
        {messages.map((msg, i) => (
          <ChatMessage key={`${msg.role}-${i}`} {...msg} />
        ))}
        <div ref={endRef} />
      </div>
      <form className="chat-input" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={streaming ? '...' : 'اكتب سؤالك هنا...'}
          disabled={streaming}
          maxLength={2000}
        />
        <button type="submit" disabled={streaming || !input.trim()}>
          {streaming ? '...' : 'إرسال'}
        </button>
      </form>
    </div>
  )
}
