import React, { useState, useEffect } from 'react'
import DocumentUpload from './components/DocumentUpload'
import DocumentList from './components/DocumentList'
import SettingsPanel from './components/SettingsPanel'
import SetupWizard from './components/SetupWizard'
import ChatInterface from './components/ChatInterface'
import AuthPage from './components/AuthPage'
import { api } from './api/client'
import { useAuth } from './auth/AuthContext'
import { IconUser, IconClock } from './components/Icons'

function SettingsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  )
}

export default function App() {
  const { user, settings, booting, logout } = useAuth()
  const [refreshKey, setRefreshKey] = useState(0)
  const [showSettings, setShowSettings] = useState(false)
  const [embedderState, setEmbedderState] = useState('not_loaded')

  useEffect(() => { api.init() }, [])

  useEffect(() => {
    let active = true

    async function checkStatus() {
      try {
        const data = await api.getStatus()
        if (!active) return
        const state = data?.embedder?.state || 'not_loaded'
        setEmbedderState(state)
        if (state === 'ready' || state === 'error') return
        setTimeout(checkStatus, 2000)
      } catch {
        if (active) setTimeout(checkStatus, 3000)
      }
    }
    checkStatus()
    return () => { active = false }
  }, [])

  function handleUploaded() {
    setRefreshKey((k) => k + 1)
  }

  // PROBLEM 7 — MANDATORY registration gate.
  if (booting) {
    return (
      <div className="setup-overlay">
        <div className="setup-card">
          <div className="setup-logo"><IconClock size={40} /></div>
          <h2>جاري التحميل...</h2>
        </div>
      </div>
    )
  }

  if (!user) {
    return <AuthPage />
  }

  if (!settings?.provider) {
    return <SetupWizard onComplete={() => {}} />
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="header-content">
          <div>
            <h1>Document Assistant</h1>
            <p>ارفع مستنداتك واسأل عنها باللغة العربية</p>
          </div>
          <div className="header-actions">
            <div className="header-user">
              <span className="header-username"><span className="icon-inline"><IconUser size={14} /></span>{user.username}</span>
              <button className="header-logout-btn" onClick={logout}>
                تسجيل الخروج
              </button>
            </div>
            <button
              className="header-settings-btn"
              onClick={() => setShowSettings((s) => !s)}
            >
              <SettingsIcon />
              <span>الإعدادات</span>
            </button>
          </div>
        </div>
      </header>
      {embedderState === 'loading' && (
        <div className="status-banner status-loading">
          <span className="spinner" />
          جاري تحميل نموذج التحليل اللغوي... يرجى الانتظار
        </div>
      )}
      {embedderState === 'error' && (
        <div className="status-banner status-error">
          فشل تحميل نموذج التحليل اللغوي. تحقق من اتصال الإنترنت وأعد تشغيل السيرفر.
        </div>
      )}
      {showSettings && (
        <div style={{ marginBottom: '24px' }}>
          <SettingsPanel />
        </div>
      )}
      <div className="main-grid">
        <div>
          <DocumentUpload onUploaded={handleUploaded} embedderReady={embedderState === 'ready'} />
          <div style={{ marginTop: '24px' }}>
            <DocumentList refreshKey={refreshKey} />
          </div>
        </div>
        <ChatInterface embedderReady={embedderState === 'ready'} />
      </div>
    </div>
  )
}