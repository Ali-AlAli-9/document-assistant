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

// NOTE (Problem 7): the LLM provider + the user's Gemini key live INSIDE the
// account on the server. The browser no longer sends X-Provider / X-API-Key;
// the backend reads the account's own stored key when serving chat requests.
//
// CSRF: Django only enforces CSRF once a csrftoken cookie exists. Every
// non-safe request must therefore echo that cookie in the X-CSRFToken header;
// the value is read FRESH per request so it stays in sync with Django's
// per-response token rotation.
function buildHeaders(isFormData) {
  const headers = {}
  if (!isFormData) {
    headers['Content-Type'] = 'application/json'
  }
  const token = getCookie('csrftoken')
  if (token) {
    headers['X-CSRFToken'] = token
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
    throw buildError(data.detail, response.status)
  }
  if (response.status === 204) return null
  return response.json()
}

// The status code must survive the throw: without it the UI cannot tell an
// expired session (401) from a server fault (500), and can only show a dead
// "Request failed: 401". `detail` is kept as a property (never as the
// message) so callers decide what the user sees.
function buildError(detail, status) {
  const err = new Error(detail || `HTTP ${status}`)
  err.status = status
  err.detail = detail || ''
  return err
}

export const api = {
  init: ensureCsrf,
  getStatus: () => request('GET', '/status/'),

  // --- Problem 7: authentication (mandatory registration) ---
  register: (username, password, passwordConfirm) =>
    request('POST', '/auth/register', { username, password, password_confirm: passwordConfirm }),
  login: (username, password) =>
    request('POST', '/auth/login', { username, password }),
  logout: () => request('POST', '/auth/logout'),
  me: () => request('GET', '/auth/me'),

  // --- Per-account LLM settings (user's own Gemini key, server-side) ---
  getSettings: () => request('GET', '/settings'),
  saveSettings: (provider, geminiApiKey) =>
    request('POST', '/settings', { provider, gemini_api_key: geminiApiKey || undefined }),
  validateApiKey: (apiKey) => request('POST', '/settings/validate-key', { api_key: apiKey }),

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
      throw buildError(data.detail, response.status)
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