export class AuthApiError extends Error {
  constructor(status, operation) {
    super(`Authentication request failed (${status})`)
    this.status = status
    this.operation = operation
  }
}

async function request(operation, { body, token, signal, cookies = false, csrf } = {}) {
  const headers = { Accept: 'application/json' }
  if (body) headers['Content-Type'] = 'application/json'
  if (operation === 'session') headers['X-FieldFix-Bootstrap'] = '1'
  if (csrf) headers['X-FieldFix-CSRF'] = csrf
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`/api/auth/${operation}`, {
    method: body ? 'POST' : 'GET',
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    cache: 'no-store',
    credentials: cookies ? 'same-origin' : 'omit',
    redirect: 'error',
  })
  if (!response.ok) throw new AuthApiError(response.status, operation)
  return response.json()
}

// Web Locks serialize account changes and credential rotation across same-origin tabs.
async function browserAction(operation, body, signal) {
  const perform = async () => {
    const { csrf } = await request('session', { cookies: true, signal })
    if (!csrf && (operation === 'refresh' || operation === 'logout')) return null
    return request(operation, { body, signal, cookies: true, csrf })
  }
  return navigator.locks ? navigator.locks.request('fieldfix-browser-session', perform) : perform()
}

export const authApi = {
  register: (body, signal) => request('register', { body, signal }),
  registrationStatus: (signal) => request('registration-status', { signal }),
  changeTemporaryPassword: (body, signal) => request('change-temporary-password', { body, signal }),
  login: (body, signal) => browserAction(body.challenge ? 'otp/verify' : 'login', body, signal),
  requestCode: (email, signal) => request('otp/request', { body: { email }, signal }),
  async enroll(email, password, portal, signal) {
    const { access_token: token } = await request('login', { body: { email, password, portal }, signal })
    const { challenge } = await request('enroll/request', { token, body: { password }, signal })
    return { token, challenge }
  },
  confirm: (token, body, signal) => request('enroll/verify', { token, body, signal }),
  refresh: () => browserAction('refresh', {}),
  logout: () => browserAction('logout', {}),
  me: (token, signal) => request('me', { token, signal }),
}

export function authErrorMessage(error) {
  if (error.status === 403 && error.operation === 'register') return 'Requester registration is closed. Contact your administrator.'
  if (error.operation === 'change-temporary-password') {
    if (error.status === 401) return 'The email or temporary password is invalid or expired.'
    if (error.status === 422) return 'Check your email and choose a different password of 15–128 characters.'
  }
  if (error.status === 428) return 'Use your temporary password to set a new one before signing in.'
  if (error.status === 429) return 'Too many attempts. Wait before trying again.'
  if (error.status === 503 && ['otp/request', 'enroll/request'].includes(error.operation)) return 'Email authentication is unavailable. You can sign in with your password.'
  if (error.status === 401 && ['otp/verify', 'enroll/verify'].includes(error.operation)) return 'Invalid or expired code. Request another code if needed.'
  if (error.status === 401) {
    return error.operation === 'login' ? 'Invalid email or password.' : 'Your session could not be verified. Sign in again.'
  }
  if (error.status === 403) return 'This account does not have permission for this action.'
  if (error.status === 409 && error.operation === 'login') return 'This account has a different role. Select its assigned sign-in tab.'
  if (error.status === 409 && error.operation === 'otp/verify') return 'This account has a different role. Select its assigned tab and request a fresh code.'
  if (error.status === 409 && error.operation === 'register') return 'An account with this email already exists.'
  if (error.status === 422) {
    return error.operation === 'register'
      ? 'Check your name, email, and password. Passwords must be 15–128 characters.'
      : 'Enter a valid email address and password.'
  }
  return 'The authentication service is unavailable. Try again shortly.'
}
