import React, { useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { IconLock } from './Icons'
import { friendlyError } from '../api/errors'

export default function AuthPage() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function switchMode(next) {
    setMode(next)
    setError('')
    setPassword('')
    setPasswordConfirm('')
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (busy) return
    setError('')
    setBusy(true)
    try {
      if (mode === 'register') {
        await register(username.trim(), password, passwordConfirm)
      } else {
        await login(username.trim(), password)
      }
      // On success AuthContext stores the user; App re-renders into the app.
    } catch (err) {
      setError(friendlyError(err, 'تعذّر الاتصال بالخادم — تأكد أن الخادم يعمل ثم أعد المحاولة.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="setup-overlay">
      <div className="setup-card auth-card">
        <div className="setup-logo setup-logo--lock"><IconLock size={40} /></div>
        <h1>Document Assistant</h1>
        <p className="setup-subtitle">التسجيل إلزامي لاستخدام المساعد</p>

        <div className="auth-tabs">
          <button
            type="button"
            className={`auth-tab ${mode === 'login' ? 'active' : ''}`}
            onClick={() => switchMode('login')}
          >
            تسجيل الدخول
          </button>
          <button
            type="button"
            className={`auth-tab ${mode === 'register' ? 'active' : ''}`}
            onClick={() => switchMode('register')}
          >
            حساب جديد
          </button>
        </div>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label className="auth-label">
            اسم المستخدم
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="اسم المستخدم"
              autoComplete="username"
              dir="rtl"
            />
          </label>

          <label className="auth-label">
            كلمة المرور
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              dir="ltr"
            />
          </label>

          {mode === 'register' && (
            <>
              <label className="auth-label">
                تأكيد كلمة المرور
                <input
                  type="password"
                  value={passwordConfirm}
                  onChange={(e) => setPasswordConfirm(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="new-password"
                  dir="ltr"
                />
              </label>
              <p className="auth-hint">
                كلمة المرور: 8 أحرف على الأقل، وليست كلمة شائعة أو أرقامًا متتالية.
              </p>
            </>
          )}

          {error && <div className="setup-error">{error}</div>}

          <button
            type="submit"
            className="btn-primary setup-start"
            disabled={busy || !username.trim() || !password || (mode === 'register' && !passwordConfirm)}
          >
            {busy ? 'الرجاء الانتظار...' : mode === 'login' ? 'دخول' : 'إنشاء الحساب'}
          </button>
        </form>

        {mode === 'register' && (
          <p className="auth-hint">
            بعد إنشاء الحساب ستدخل مفتاح Gemini الخاص بك من الإعدادات (أو تستخدم
            Ollama محليًّا بدون مفتاح).
          </p>
        )}
      </div>
    </div>
  )
}