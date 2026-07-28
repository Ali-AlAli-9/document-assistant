const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

let csrfInitialized = false
let csrfRetries = 0
const CSRF_MAX_RETRIES = 3

function getCookie(name) {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
  return null
}

async function ensureCsrf() {
  if (csrfInitialized || getCookie('csrftoken')) return
  if (csrfRetries >= CSRF_MAX_RETRIES) return
  try {
    await fetch(`${API_BASE}/csrf/`, { credentials: 'include' })
    csrfInitialized = true
  } catch {
    csrfRetries++
    csrfInitialized = false
  }
}

function getProvider() {
  return localStorage.getItem('llm_provider') || 'gemini'
}

function getApiKey() {
  const provider = getProvider()
  if (provider === 'gemini') {
    return localStorage.getItem('gemini_api_key') || ''
  }
  return ''
}

function buildHeaders(isFormData) {
  const headers = {}
  const provider = getProvider()
  headers['X-Provider'] = provider
  const apiKey = getApiKey()
  if (apiKey) headers['X-API-Key'] = apiKey
  if (!isFormData) {
    headers['Content-Type'] = 'application/json'
  }
  return headers
}

async function request(method, path, body, isFormData) {
  await ensureCsrf()
  const headers = buildHeaders(isFormData)
  const options = { method, headers, credentials: 'include' }
  if (body) {
    options.body = isFormData ? body : JSON.stringify(body)
  }
  const response = await fetch(`${API_BASE}${path}`, options)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || `Request failed: ${response.status}`)
  }
  if (response.status === 204) return null
  return response.json()
}

export const api = {
  init: ensureCsrf,
  getStatus: () => request('GET', '/status/'),
  getDocuments: () => request('GET', '/documents'),
  getDocument: (id) => request('GET', `/documents/${id}`),
  uploadDocument: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('POST', '/documents/upload', form, true)
  },
  deleteDocument: (id) => request('DELETE', `/documents/${id}`),
  retryDocument: (id) => request('POST', `/documents/${id}/retry`),
  askQuestion: (question, conversationId) =>
    request('POST', '/chat/ask', { question, conversation_id: conversationId || null }),
  validateApiKey: (apiKey) => request('POST', '/settings/validate-key', { api_key: apiKey }),
  askQuestionStream: async function* (question, conversationId) {
    await ensureCsrf()
    const headers = buildHeaders(false)
    const response = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ question, conversation_id: conversationId || null }),
      credentials: 'include',
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(data.detail || `Stream failed: ${response.status}`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            yield data
          } catch {}
        }
      }
    }
  },
}
