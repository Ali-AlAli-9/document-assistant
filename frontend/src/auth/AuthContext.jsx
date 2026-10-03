import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null) // { username, has_gemini_key, llm_provider } | null
  const [settings, setSettings] = useState(null) // { provider, has_gemini_key } | null
  const [booting, setBooting] = useState(true)

  const reloadSettings = useCallback(async () => {
    try {
      const s = await api.getSettings()
      setSettings(s)
      return s
    } catch {
      setSettings(null)
      return null
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const me = await api.me()
      setUser(me)
      await reloadSettings()
    } catch {
      setUser(null)
      setSettings(null)
    }
  }, [reloadSettings])

  useEffect(() => {
    let active = true
    ;(async () => {
      await api.init()
      await refresh()
      if (active) setBooting(false)
    })()
    return () => { active = false }
  }, [refresh])

  const login = useCallback(async (username, password) => {
    const data = await api.login(username, password)
    setUser(data)
    await reloadSettings()
    return data
  }, [reloadSettings])

  const register = useCallback(async (username, password, passwordConfirm) => {
    const data = await api.register(username, password, passwordConfirm)
    setUser(data)
    await reloadSettings()
    return data
  }, [reloadSettings])

  const logout = useCallback(async () => {
    try { await api.logout() } catch {}
    setUser(null)
    setSettings(null)
  }, [])

  return (
    <AuthContext.Provider value={{
      user,
      settings,
      booting,
      login,
      register,
      logout,
      reloadSettings,
      refresh,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}