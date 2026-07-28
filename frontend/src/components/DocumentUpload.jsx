import React, { useState, useRef } from 'react'
import { api } from '../api/client'

const ALLOWED_EXTENSIONS = ['.pdf', '.txt', '.docx']
const MAX_SIZE = 50 * 1024 * 1024

function UploadIcon() {
  return (
    <svg className="upload-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 16V4" />
      <path d="M8 8l4-4 4 4" />
      <path d="M20 21H4" />
    </svg>
  )
}

export default function DocumentUpload({ onUploaded, embedderReady }) {
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const inputRef = useRef(null)

  function validate(file) {
    const ext = '.' + file.name.split('.').pop().toLowerCase()
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      return 'الملفات المسموحة فقط: PDF، TXT، DOCX'
    }
    if (file.size > MAX_SIZE) {
      return 'حجم الملف يتجاوز 50MB'
    }
    return null
  }

  async function handleFile(file) {
    setError('')
    setSuccess('')
    const validationError = validate(file)
    if (validationError) {
      setError(validationError)
      return
    }
    setUploading(true)
    try {
      const result = await api.uploadDocument(file)
      setSuccess(`تم رفع "${result.title || file.name}" بنجاح`)
      setTimeout(() => setSuccess(''), 4000)
      onUploaded?.(result)
    } catch (err) {
      setError(err.message || 'فشل رفع الملف')
    } finally {
      setUploading(false)
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (file) handleFile(file)
  }

  function handleChange(e) {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    e.target.value = ''
  }

  return (
    <div className="card">
      <h2>رفع مستند</h2>
      {error && <div className="error-message">{error}</div>}
      {success && <div className="success-message">{success}</div>}
      <div
        className={`upload-zone ${dragOver ? 'drag-over' : ''} ${!embedderReady ? 'upload-disabled' : ''}`}
        onDragOver={(e) => { e.preventDefault(); if (embedderReady) setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={embedderReady ? handleDrop : undefined}
        onClick={embedderReady ? () => inputRef.current?.click() : undefined}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.txt,.docx"
          onChange={handleChange}
          hidden
        />
        {uploading ? (
          <div className="upload-status">
            <span className="spinner" />
            جاري رفع الملف ومعالجته...
          </div>
        ) : !embedderReady ? (
          <div className="upload-status">
            <span className="spinner" />
            يرجى الانتظار حتى تحميل نموذج التحليل اللغوي...
          </div>
        ) : (
          <>
            <UploadIcon />
            <p>اسحب وأفلت الملف هنا أو انقر للاختيار</p>
            <span className="upload-hint">PDF, TXT, DOCX — حد أقصى 50MB</span>
          </>
        )}
      </div>
    </div>
  )
}
