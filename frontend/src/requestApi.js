import { useEffect, useState } from 'react'
import { useAuth } from './AuthContext.jsx'

export const REQUESTS_PATH = '/api/requests'

export async function requestMaintenance(authorizedFetch, path, options) {
  const response = await authorizedFetch(path, options)
  if (response.ok) return response
  if (response.status === 401) throw new Error('Sign in to continue.')
  if (response.status === 403) throw new Error('Your account cannot access this resource.')
  if ([404, 409, 413, 415, 422, 503].includes(response.status)) {
    const data = await response.json().catch(() => null)
    if (typeof data?.detail === 'string') throw new Error(data.detail)
    if (response.status === 404) throw new Error('This record could not be found.')
    if (response.status === 409) throw new Error('The record changed. Refresh and try again.')
    if (response.status === 503) throw new Error('The service is temporarily unavailable. Try again shortly.')
    throw new Error('Check the request fields and try again.')
  }
  throw new Error('The maintenance service is unavailable. Try again shortly.')
}

export function useRequestData(path) {
  const { authorizedFetch } = useAuth()
  const [result, setResult] = useState(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const response = await requestMaintenance(authorizedFetch, path, { signal: controller.signal })
        const data = await response.json()
        if (!controller.signal.aborted) setResult({ path, attempt, data })
      } catch (error) {
        if (!controller.signal.aborted) setResult({ path, attempt, error: maintenanceErrorMessage(error) })
      }
    }
    load()
    return () => controller.abort()
  }, [authorizedFetch, path, attempt])

  const current = result?.path === path && result.attempt === attempt ? result : null
  return { data: current?.data, error: current?.error, loading: !current, retry: () => setAttempt((value) => value + 1) }
}

export function maintenanceErrorMessage(error) {
  return error instanceof TypeError || error instanceof SyntaxError
    ? 'The maintenance service is unavailable. Try again shortly.'
    : error.message
}
