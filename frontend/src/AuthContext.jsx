import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { AuthApiError, authApi } from './authApi.js'
import { portals } from './portals.js'

const AuthContext = createContext(null)
const expiredMessage = 'Your session expired. Sign in again.'
const invalidMessage = 'Your session is no longer valid. Sign in again.'

function getExpiry(token) {
  try {
    // The decoded payload schedules the UI timeout. FastAPI verifies the token and identity.
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const expiresAt = JSON.parse(atob(payload)).exp * 1000
    if (Number.isSafeInteger(expiresAt) && expiresAt > Date.now()) return expiresAt
  } catch {
    // A malformed token cannot start a session.
  }
  throw new AuthApiError(401, 'me')
}

async function getProfile(token, signal) {
  const user = await authApi.me(token, signal)
  if (!portals.some(({ id }) => id === user?.role)) throw new AuthApiError(403, 'me')
  return user
}

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState({ session: null, message: '' })
  const [starting, setStarting] = useState(true)
  const [logoutPending, setLogoutPending] = useState(false)
  const generation = useRef(0)
  const channel = useRef(null)
  const restoration = useRef(null)
  const token = auth.session?.token
  const expiresAt = auth.session?.expiresAt

  const restore = useCallback(async () => {
    if (restoration.current) return restoration.current
    const version = generation.current
    const task = (async () => {
      const result = await authApi.refresh()
      if (generation.current !== version) return
      if (!result) {
        setAuth((current) => current.session ? { session: null, message: expiredMessage } : current)
        return
      }
      const user = await getProfile(result.access_token)
      const expiresAt = getExpiry(result.access_token)
      if (generation.current === version) {
        setAuth({ session: { token: result.access_token, user, expiresAt, remember: true }, message: '' })
      }
    })().catch((error) => {
      if (generation.current === version) setAuth({ session: null, message: error.status === 401
        ? expiredMessage : 'Could not restore your session. Sign in or reload to retry.' })
    }).finally(() => { restoration.current = null })
    restoration.current = task
    return task
  }, [])

  useEffect(() => {
    restore().finally(() => setStarting(false))
    const bus = typeof BroadcastChannel === 'function' ? new BroadcastChannel('fieldfix-auth') : null
    channel.current = bus
    if (bus) bus.onmessage = () => {
      generation.current++
      setAuth({ session: null, message: 'The sign-in session changed in another tab.' })
    }
    return () => { bus?.close(); channel.current = null }
  }, [restore])

  async function signIn(credentials, signal) {
    const version = ++generation.current
    const result = await authApi.login(credentials, signal)
    const user = await getProfile(result.access_token, signal)
    const expiry = getExpiry(result.access_token)
    signal.throwIfAborted()
    if (version !== generation.current) return
    channel.current?.postMessage('account changed')
    setLogoutPending(false)
    setAuth({ session: { token: result.access_token, user, expiresAt: expiry, remember: credentials.remember }, message: '' })
  }

  async function signOut() {
    generation.current++
    setAuth({ session: null, message: 'Signing out…' })
    channel.current?.postMessage('signed out')
    try {
      await authApi.logout()
      setLogoutPending(false)
      setAuth({ session: null, message: 'You signed out.' })
    } catch {
      setLogoutPending(true)
      setAuth({ session: null, message: 'Server sign-out could not be confirmed. Reconnect and retry before leaving this device.' })
    }
  }

  const authorizedFetch = useCallback(async (path, options = {}) => {
    const url = new URL(path, window.location.origin)
    if (!token || url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) {
      throw new AuthApiError(401, 'request')
    }
    const headers = new Headers(options.headers)
    headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(url, {
      ...options, headers, cache: 'no-store', credentials: 'omit', redirect: 'error',
    })
    if (response.status === 401) {
      setAuth((current) => current.session?.token === token ? { session: null, message: invalidMessage } : current)
    }
    return response
  }, [token])

  useEffect(() => {
    if (!token) return

    const controller = new AbortController()
    let checking = false

    function clearSession(message) {
      setAuth((current) => current.session?.token === token ? { session: null, message } : current)
    }

    function expire() {
      if (auth.session.remember) restore()
      else clearSession(expiredMessage)
    }

    function updateProfile(profile) {
      setAuth((current) => current.session?.token === token
        ? { ...current, session: { ...current.session, ...profile } }
        : current)
    }

    async function recheckSession() {
      if (Date.now() >= expiresAt) {
        expire()
        return
      }
      if (checking) return
      checking = true
      try {
        const user = await getProfile(token, controller.signal)
        if (!controller.signal.aborted) updateProfile({ user, accessDenied: false })
      } catch (error) {
        if (controller.signal.aborted) return
        if (error.status === 401) clearSession(invalidMessage)
        if (error.status === 403) updateProfile({ accessDenied: true })
      } finally {
        checking = false
      }
    }

    const timer = window.setTimeout(expire, Math.max(0, expiresAt - Date.now()))
    window.addEventListener('focus', recheckSession)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('focus', recheckSession)
      controller.abort()
    }
  }, [token, expiresAt, auth.session?.remember, restore])

  return (
    <AuthContext.Provider value={{
      starting, logoutPending,
      user: auth.session?.user,
      accessDenied: auth.session?.accessDenied,
      sessionMessage: auth.message,
      authorizedFetch,
      updateDisplayName: (full_name) => setAuth(current => current.session?.token === token && current.session ? {
        ...current, session: { ...current.session, user: { ...current.session.user, full_name } },
      } : current),
      signIn,
      signOut,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
