import React, { useState } from 'react'
import { api } from '../api/client'
import { friendlyError } from '../api/errors'
import { useAuth } from '../auth/AuthContext'
import { IconCloud, IconMonitor, IconCheck } from './Icons'

export default function SettingsPanel() {
  const { settings, reloadSettings } = useAuth()
  const [geminiKey, setGeminiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(null)

  const provider = settings?.provider || 'gemini'
  const hasKey = Boolean(settings?.has_gemini_key)

  async function handleProviderChange(newProvider) {
    setSaving(true)
    setStatus(null)
    try {
      await api.saveSettings(newProvider, undefined)
      await reloadSettings()
      setStatus({ type: 'success', text: newProvider === 'gemini' ? 'تم اختيار Gemini' : 'تم اختيار Ollama' })
    } catch (err) {
      setStatus({ type: 'error', text: friendlyError(err, 'تعذّر الحفظ — أعد المحاولة.') })
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveKey() {
    const trimmed = geminiKey.trim()
    if (!trimmed) {
      setStatus({ type: 'error', text: 'أدخل المفتاح أولاً' })
      return
    }
    setTesting(true)
    setStatus(null)
    try {
      const result = await api.validateApiKey(trimmed)
      if (!result.valid) {
        setStatus({ type: 'error', text: result.detail || 'المفتاح غير صالح' })
        return
      }
      // Stored INSIDE the account on the server — never echoed back.
      await api.saveSettings('gemini', trimmed)
      await reloadSettings()
      setGeminiKey('')
      setStatus({ type: 'success', text: 'تم حفظ مفتاح Gemini داخل حسابك' })
    } catch (err) {
      setStatus({ type: 'error', text: friendlyError(err, 'فشل الاتصال — تأكد من الشبكة ثم أعد المحاولة.') })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="card settings-panel">
      <h2>الإعدادات</h2>

      <div className="settings-field">
        <label>مزود LLM</label>
        <div className="provider-selector">
          <button
            className={`provider-btn ${provider === 'gemini' ? 'active' : ''}`}
            onClick={() => handleProviderChange('gemini')}
            disabled={saving}
          >
            <span className="icon-inline"><IconCloud size={15} /></span>Gemini (سحابي)
          </button>
          <button
            className={`provider-btn ${provider === 'ollama' ? 'active' : ''}`}
            onClick={() => handleProviderChange('ollama')}
            disabled={saving}
          >
            <span className="icon-inline"><IconMonitor size={15} /></span>Ollama (محلي)
          </button>
        </div>
      </div>

      {provider === 'gemini' ? (
        <div className="settings-field">
          <label htmlFor="api-key">مفتاح Gemini API (خاص بك)</label>
          {hasKey && (
            <p className="settings-hint"><span className="icon-inline"><IconCheck size={14} /></span>لديك مفتاح محفوظ داخل حسابك — أدخل مفتاحًا جديدًا لاستبداله.</p>
          )}
          <input
            id="api-key"
            type="password"
            value={geminiKey}
            onChange={(e) => { setGeminiKey(e.target.value); setStatus(null) }}
            placeholder="أدخل مفتاح Gemini API الخاص بك..."
            dir="ltr"
          />
          <p className="settings-hint">احصل على مفتاح مجاني من aistudio.google.com</p>
          <p className="settings-hint">يُحفظ المفتاح داخل حسابك على الخادم ولا يُعرض مرة أخرى.</p>
          <div className="settings-actions">
            <button onClick={handleSaveKey} disabled={testing || !geminiKey.trim()} className="btn-primary">
              {testing ? 'جاري الاختبار...' : 'حفظ المفتاح'}
            </button>
          </div>
        </div>
      ) : (
        <div className="settings-field">
          <div className="ollama-info">
            <p><span className="icon-inline"><IconCheck size={14} /></span>Ollama يعمل محلياً — لا يحتاج مفتاح API</p>
            <p className="settings-hint">تأكد من تشغيل Ollama على جهازك (<code>ollama serve</code>)</p>
          </div>
        </div>
      )}

      {status && (
        <div className={`settings-status ${status.type}`}>
          {status.text}
        </div>
      )}
    </div>
  )
}