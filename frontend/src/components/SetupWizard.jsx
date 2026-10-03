import React, { useState } from 'react'
import { api } from '../api/client'
import { friendlyError } from '../api/errors'
import { useAuth } from '../auth/AuthContext'
import { IconDocument, IconCloud, IconMonitor, IconCheckCircle } from './Icons'

export default function SetupWizard({ onComplete }) {
  const { reloadSettings } = useAuth()
  const [step, setStep] = useState('welcome')
  const [geminiKey, setGeminiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')

  async function finish(provider, key) {
    setStep('done')
    try {
      await api.saveSettings(provider, key)
      await reloadSettings()
      onComplete()
    } catch (err) {
      setError(friendlyError(err, 'فشل حفظ الإعدادات على الخادم — أعد المحاولة.'))
    }
  }

  function handleChooseProvider(choice) {
    if (choice === 'ollama') {
      finish('ollama')
    } else {
      setStep('gemini-key')
    }
  }

  async function handleTestKey() {
    const trimmed = geminiKey.trim()
    if (!trimmed) return
    setTesting(true)
    setError('')
    try {
      const result = await api.validateApiKey(trimmed)
      if (result.valid) {
        // The user's OWN key is stored inside his account on the server.
        await finish('gemini', trimmed)
      } else {
        setError(result.detail || 'المفتاح غير صالح')
      }
    } catch (err) {
      setError(friendlyError(err, 'فشل الاتصال — تأكد من الشبكة ثم أعد المحاولة.'))
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="setup-overlay">
      <div className="setup-card">
        {step === 'welcome' && (
          <>
            <div className="setup-logo"><IconDocument size={44} /></div>
            <h1>Document Assistant</h1>
            <p className="setup-subtitle">استفسر عن مستنداتك بالعربية</p>
            <p className="setup-desc">
              اختر مزود LLM للبدء. يمكنك تغيير هذا لاحقاً من الإعدادات، ويُحفظ
              اختيارك داخل حسابك.
            </p>
            <div className="setup-choices">
              <button className="setup-choice" onClick={() => handleChooseProvider('gemini')}>
                <span className="setup-choice-icon"><IconCloud size={30} /></span>
                <span className="setup-choice-title">Gemini (سحابي)</span>
                <span className="setup-choice-desc">مجاني 1500 req/day — يتطلب مفتاحك الخاص</span>
              </button>
              <button className="setup-choice" onClick={() => handleChooseProvider('ollama')}>
                <span className="setup-choice-icon"><IconMonitor size={30} /></span>
                <span className="setup-choice-title">Ollama (محلي)</span>
                <span className="setup-choice-desc">مجاني — خصوصية تامة — لا يحتاج إنترنت</span>
              </button>
            </div>
          </>
        )}

        {step === 'gemini-key' && (
          <>
            <div className="setup-logo"><IconCloud size={44} /></div>
            <h2>إعداد Gemini</h2>
            <p className="setup-desc">
              أدخل مفتاح Gemini API الخاص بك. احصل على مفتاح مجاني من:
              <br />
              <a href="https://aistudio.google.com" target="_blank" rel="noopener noreferrer" className="setup-link">
                aistudio.google.com
              </a>
            </p>
            <div className="setup-key-input">
              <input
                type="password"
                value={geminiKey}
                onChange={(e) => { setGeminiKey(e.target.value); setError('') }}
                placeholder="أدخل مفتاح Gemini API الخاص بك..."
                dir="ltr"
              />
              <div className="setup-key-actions">
                <button onClick={handleTestKey} disabled={testing || !geminiKey.trim()} className="btn-primary">
                  {testing ? 'جاري الاختبار...' : 'اختبار المفتاح'}
                </button>
              </div>
              {error && <div className="setup-error">{error}</div>}
              <p className="auth-hint">
                يُحفظ المفتاح داخل حسابك على الخادم — لا يُظهره التطبيق مرة أخرى
                لأي شخص.
              </p>
            </div>
          </>
        )}

        {step === 'done' && (
          <>
            <div className="setup-logo"><IconCheckCircle size={44} /></div>
            <h2>تم الإعداد!</h2>
            <p className="setup-desc">
              يمكنك الآن رفع المستندات وطرح الأسئلة.
            </p>
            {error && <div className="setup-error">{error}</div>}
            <button className="btn-primary setup-start" onClick={onComplete}>
              ابدأ الآن
            </button>
          </>
        )}
      </div>
    </div>
  )
}