import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

const STATUS_LABELS = {
  pending: 'قيد الانتظار',
  processing: 'قيد المعالجة',
  ready: 'جاهز',
  failed: 'فشل',
}

function FileIcon({ ext }) {
  const color = ext === '.pdf' ? '#dc2626' : ext === '.docx' ? '#2563eb' : '#64748b'
  return (
    <svg className="doc-file-icon" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10 9 9 9 8 9" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  )
}

function RefreshIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  )
}

function SkeletonLoader() {
  return (
    <div>
      {[1, 2, 3].map((i) => (
        <div key={i} className="skeleton-item">
          <div className="skeleton skeleton-icon" />
          <div className="skeleton-lines">
            <div className="skeleton skeleton-text" />
            <div className="skeleton skeleton-text" />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function DocumentList({ refreshKey }) {
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const fetchDocs = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const docs = await api.getDocuments()
      setDocuments(docs)
    } catch (err) {
      setError(err.message || 'فشل تحميل المستندات')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchDocs() }, [fetchDocs, refreshKey])

  async function handleDelete(id) {
    const doc = documents.find((d) => d.id === id)
    const name = doc?.title || 'هذا المستند'
    if (!confirm(`هل أنت متأكد من حذف "${name}"؟`)) return
    try {
      await api.deleteDocument(id)
      setDocuments((prev) => prev.filter((d) => d.id !== id))
      setMessage(`تم حذف "${name}" بنجاح`)
      setTimeout(() => setMessage(''), 4000)
    } catch (err) {
      setError(err.message || 'فشل حذف المستند')
    }
  }

  async function handleRetry(id) {
    try {
      await api.retryDocument(id)
      setDocuments((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: 'processing' } : d))
      )
      setMessage('تمت إعادة المعالجة')
      setTimeout(() => setMessage(''), 4000)
    } catch (err) {
      setError(err.message || 'فشلت إعادة المحاولة')
    }
  }

  return (
    <div className="card">
      <h2>المستندات المرفوعة</h2>
      {error && <div className="error-message">{error}</div>}
      {message && <div className="success-message">{message}</div>}
      {loading ? (
        <SkeletonLoader />
      ) : documents.length === 0 ? (
        <div className="empty-state">لا توجد مستندات مرفوعة بعد</div>
      ) : (
        <ul className="doc-list">
          {documents.map((doc) => {
            const ext = '.' + doc.title.split('.').pop().toLowerCase()
            return (
              <li key={doc.id} className="doc-item">
                <div className="doc-info">
                  <FileIcon ext={ext} />
                  <span className="doc-title" title={doc.title}>{doc.title}</span>
                  <span className={`doc-status status-${doc.status}`}>
                    {STATUS_LABELS[doc.status] || doc.status}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                  {doc.status === 'failed' && (
                    <button className="btn-retry" onClick={() => handleRetry(doc.id)} title="إعادة محاولة">
                      <RefreshIcon />
                    </button>
                  )}
                  <button className="btn-delete" onClick={() => handleDelete(doc.id)} title="حذف">
                    <TrashIcon />
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
