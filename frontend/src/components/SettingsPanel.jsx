import React, { useState, useEffect } from 'react'
import { api } from '../api/client'

const PROVIDER_KEY = 'llm_provider'
const GEMINI_KEY_STORAGE = 'gemini_api_key'

export default function SettingsPanel() {
  const [provider, setProvider] = useState(localStorage.getItem(PROVIDER_KEY) || 'gemini')
  const [geminiKey, setGeminiKey] = useState(localStorage.getItem(GEMINI_KEY_STORAGE) || '')
  const [testing, setTesting] = useState(false)
  const [status, setStatus] = useState(null)

  useEffect(() => {
    const saved = localStorage.getItem(GEMINI_KEY_STORAGE)
    if (saved && provider === 'gemini') setStatus({ type: 'success', text: 'المفتاح محفوظ' })
  }, [])

  function handleProviderChange(newProvider) {
    setProvider(newProvider)
    localStorage.setItem(PROVIDER_KEY, newProvider)
    setStatus(null)
  }

  function handleSave() {
    const trimmed = geminiKey.trim()
    if (trimmed) {
      localStorage.setItem(GEMINI_KEY_STORAGE, trimmed)
      setStatus({ type: 'success', text: 'تم حفظ المفتاح' })
    } else {
      localStorage.removeItem(GEMINI_KEY_STORAGE)
      setStatus({ type: '', text: 'تم إزالة المفتاح' })
    }
  }

  async function handleTest() {
    const trimmed = geminiKey.trim()
    if (!trimmed) {
      setStatus({ type: 'error', text: 'الرجاء إدخال المفتاح أولاً' })
      return
    }
    setTesting(true)
    setStatus(null)
    try {
      const result = await api.validateApiKey(trimmed)
      if (result.valid) {
        setStatus({ type: 'success', text: result.detail || 'المفتاح شغال بنجاح' })
        localStorage.setItem(GEMINI_KEY_STORAGE, trimmed)
      } else {
        setStatus({ type: 'error', text: result.detail || 'المفتاح غير صالح' })
      }
    } catch (err) {
      setStatus({ type: 'error', text: err.message || 'فشل الاتصال' })
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
          >
            ☁️ Gemini (سحابي)
          </button>
          <button
            className={`provider-btn ${provider === 'ollama' ? 'active' : ''}`}
            onClick={() => handleProviderChange('ollama')}
          >
            💻 Ollama (محلي)
          </button>
        </div>
      </div>

      {provider === 'gemini' ? (
        <div className="settings-field">
          <label htmlFor="api-key">مفتاح Gemini API</label>
          <input
            id="api-key"
            type="password"
            value={geminiKey}
            onChange={(e) => { setGeminiKey(e.target.value); setStatus(null) }}
            placeholder="أدخل مفتاح Gemini API..."
            dir="ltr"
          />
          <p className="settings-hint">احصل على مفتاح مجاني من aistudio.google.com</p>
          <div className="settings-actions">
            <button onClick={handleSave} className="btn-secondary">حفظ</button>
            <button onClick={handleTest} disabled={testing || !geminiKey.trim()} className="btn-primary">
              {testing ? 'جاري الاختبار...' : 'اختبار'}
            </button>
          </div>
        </div>
      ) : (
        <div className="settings-field">
          <div className="ollama-info">
            <p>✅ Ollama يعمل محلياً — لا يحتاج مفتاح API</p>
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
