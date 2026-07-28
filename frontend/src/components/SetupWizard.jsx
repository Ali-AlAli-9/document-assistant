import React, { useState } from 'react'
import { api } from '../api/client'

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState('welcome')
  const [provider, setProvider] = useState(null)
  const [geminiKey, setGeminiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')

  function handleChooseProvider(choice) {
    setProvider(choice)
    if (choice === 'ollama') {
      localStorage.setItem('llm_provider', 'ollama')
      setStep('ollama-done')
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
        localStorage.setItem('llm_provider', 'gemini')
        localStorage.setItem('gemini_api_key', trimmed)
        setStep('done')
      } else {
        setError(result.detail || 'المفتاح غير صالح')
      }
    } catch (err) {
      setError(err.message || 'فشل الاتصال')
    } finally {
      setTesting(false)
    }
  }

  function handleSkip() {
    localStorage.setItem('llm_provider', 'gemini')
    setStep('done')
  }

  return (
    <div className="setup-overlay">
      <div className="setup-card">
        {step === 'welcome' && (
          <>
            <div className="setup-logo">📄</div>
            <h1>Document Assistant</h1>
            <p className="setup-subtitle">استفسر عن مستنداتك بالعربية</p>
            <p className="setup-desc">
              اختر مزود LLM للبدء. يمكنك تغيير هذا لاحقاً من الإعدادات.
            </p>
            <div className="setup-choices">
              <button className="setup-choice" onClick={() => handleChooseProvider('gemini')}>
                <span className="setup-choice-icon">☁️</span>
                <span className="setup-choice-title">Gemini (سحابي)</span>
                <span className="setup-choice-desc">مجاني 1500 req/day — يحتاج مفتاح API</span>
              </button>
              <button className="setup-choice" onClick={() => handleChooseProvider('ollama')}>
                <span className="setup-choice-icon">💻</span>
                <span className="setup-choice-title">Ollama (محلي)</span>
                <span className="setup-choice-desc">مجاني — خصوصية تامة — لا يحتاج إنترنت</span>
              </button>
            </div>
          </>
        )}

        {step === 'gemini-key' && (
          <>
            <div className="setup-logo">☁️</div>
            <h2>إعداد Gemini</h2>
            <p className="setup-desc">
              أدخل مفتاح Gemini API. احصل على مفتاح مجاني من:
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
                placeholder="أدخل مفتاح Gemini API..."
                dir="ltr"
              />
              <div className="setup-key-actions">
                <button onClick={handleTestKey} disabled={testing || !geminiKey.trim()} className="btn-primary">
                  {testing ? 'جاري الاختبار...' : 'اختبار المفتاح'}
                </button>
                <button onClick={handleSkip} className="btn-secondary">
                  تخطي
                </button>
              </div>
              {error && <div className="setup-error">{error}</div>}
            </div>
          </>
        )}

        {step === 'ollama-done' && (
          <>
            <div className="setup-logo">💻</div>
            <h2>Ollama</h2>
            <p className="setup-desc">
              تأكد من تشغيل Ollama على جهازك والنموذج مُحمّل:
            </p>
            <div className="setup-code">
              <pre>ollama pull llama3.2:3b</pre>
            </div>
            <button className="btn-primary setup-start" onClick={onComplete}>
              البدء
            </button>
          </>
        )}

        {step === 'done' && (
          <>
            <div className="setup-logo">✅</div>
            <h2>تم الإعداد!</h2>
            <p className="setup-desc">
              يمكنك الآن رفع المستندات وطرح الأسئلة.
            </p>
            <button className="btn-primary setup-start" onClick={onComplete}>
              ابدأ الآن
            </button>
          </>
        )}
      </div>
    </div>
  )
}
