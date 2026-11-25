import { ArrowLeft, ArrowRight, Eye, EyeOff, KeyRound, LockKeyhole, Mail, UserRound, Wrench } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import { authApi, authErrorMessage } from './authApi.js'
import { portals } from './portals.js'

const roles = portals.map(({ id, label }) => ({ id, label: id === 'admin' ? 'Admin' : label }))

function LoginCard({ children }) {
  return (
    <main className="login-page">
      <section className="glass login-card" aria-labelledby="login-title">
        {children}
      </section>
    </main>
  )
}

function SignInOptions({ name, title, options, selected, onChange, disabled }) {
  return (
    <fieldset className={name === 'sign-in-method' ? 'login-choice login-choice-method' : 'login-choice'} disabled={disabled}>
      <legend className="sr-only">{title}</legend>
      {name === 'sign-in-method' && <span className="login-method-title" aria-hidden="true">{title}</span>}
      <div className="login-segments">
        {options.map(({ id, label }) => (
          <label className="login-option" key={id}>
            <input className="sr-only" type="radio" name={name} value={id} checked={selected === id} onChange={() => onChange(id)} />
            <span className="login-option-label">{label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function useAuthSubmission(setStatus) {
  const [busy, setBusy] = useState(false)
  const pending = useRef(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function submit(action) {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setStatus('')

    try {
      await action(controller.signal)
    } catch (error) {
      if (!controller.signal.aborted) setStatus(authErrorMessage(error))
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return { busy, submit }
}

export function PasswordResetPage() {
  const { state } = useLocation()
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const { busy, submit } = useAuthSubmission(setStatus)

  function changePassword(event) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const password = data.get('new_password')
    if (password !== data.get('confirm_password')) {
      setStatus('The new passwords do not match.')
      return
    }
    submit(async (signal) => {
      await authApi.changeTemporaryPassword({
        email: data.get('email').trim(), temporary_password: data.get('temporary_password'), new_password: password,
      }, signal)
      navigate('/login', { replace: true, state: { passwordChanged: true, portal: state?.portal } })
    })
  }

  return (
    <LoginCard>
      <div className="login-brand">
        <span className="brand-emblem" aria-hidden="true"><KeyRound size={27} /></span>
        <div>
          <h1 id="login-title">Set a new password</h1>
          <p>FieldFix AI 2.0</p>
        </div>
      </div>
      <p>Use the temporary password you received from your Administrator.</p>
      <form className="login-form" onSubmit={changePassword}>
        <fieldset disabled={busy}>
          <div className="login-field">
            <label htmlFor="reset-email">Email address</label>
            <div className="dark-input"><input id="reset-email" name="email" type="email" autoComplete="username" required defaultValue={state?.email} /></div>
          </div>
          {[
            ['temporary_password', 'Temporary password', 'current-password'],
            ['new_password', 'New password (15–128 characters)', 'new-password'],
            ['confirm_password', 'Confirm new password', 'new-password'],
          ].map(([name, label, autocomplete]) => (
            <div key={name} className="login-field">
              <label htmlFor={name}>{label}</label>
              <div className="dark-input"><input id={name} type="password" name={name} autoComplete={autocomplete}
                required minLength={name === 'temporary_password' ? undefined : 15} maxLength={128} /></div>
            </div>
          ))}
        </fieldset>
        <button type="submit" className="primary-action" disabled={busy}>Set password</button>
        <p className="login-status" role="status">{status}</p>
      </form>
      <Link className="login-link auth-back-link" to="/login"><ArrowLeft size={17} aria-hidden="true" /> Back to sign-in</Link>
    </LoginCard>
  )
}

export function RegisterPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const { busy, submit } = useAuthSubmission(setStatus)
  const [registration, setRegistration] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    authApi.registrationStatus(controller.signal).then(({ allowed }) => {
      if (!controller.signal.aborted) setRegistration(allowed)
    }).catch(() => {
      if (!controller.signal.aborted) setStatus('Could not check registration. You can retry or contact your administrator.')
    })
    return () => controller.abort()
  }, [])

  function handleSubmit(event) {
    event.preventDefault()
    if (busy || registration === false) return
    const form = event.currentTarget
    const data = new FormData(form)
    const fullName = data.get('full_name').trim()
    if (!fullName) {
      setStatus('Enter your full name.')
      form.elements.full_name.focus()
      return
    }

    submit(async (signal) => {
      let profile
      try {
        profile = await authApi.register({
          full_name: fullName, email: data.get('email').trim(), password: data.get('password'),
        }, signal)
      } catch (error) {
        if (error.status === 403) setRegistration(false)
        throw error
      }
      signal.throwIfAborted()
      form.reset()
      navigate('/login', { replace: true, state: { registeredEmail: profile.email } })
    })
  }

  if (user) return <Navigate replace to={`/${user.role}`} />

  return (
    <LoginCard>
      <div className="login-brand">
        <span className="brand-emblem" aria-hidden="true"><Wrench size={27} /></span>
        <div>
          <h1 id="login-title">Create a Requester Account</h1>
          <p>FieldFix AI 2.0</p>
        </div>
      </div>

      {registration === false ? <p role="status">Requester registration is closed. Contact your administrator.</p> :
      <form className="login-form" method="post" onSubmit={handleSubmit} aria-busy={busy}>
        <div className="login-field">
          <label htmlFor="register-name">Full Name</label>
          <div className="dark-input">
            <UserRound size={18} aria-hidden="true" />
            <input id="register-name" name="full_name" autoComplete="name" maxLength={160} required disabled={busy} />
          </div>
        </div>
        <div className="login-field">
          <label htmlFor="register-email">Email Address</label>
          <div className="dark-input">
            <Mail size={18} aria-hidden="true" />
            <input id="register-email" name="email" type="email" autoComplete="email" maxLength={320} required disabled={busy} />
          </div>
        </div>
        <div className="login-field">
          <label htmlFor="register-password">Password</label>
          <div className="dark-input">
            <LockKeyhole size={18} aria-hidden="true" />
            <input id="register-password" name="password" type="password" autoComplete="new-password" minLength={15} maxLength={128} required disabled={busy} aria-describedby="register-password-hint" />
          </div>
          <p className="login-remember-note" id="register-password-hint">Use 15–128 characters.</p>
        </div>
        <button className="primary-action" type="submit" disabled={busy}>
          {busy ? 'Creating account…' : 'Create Account'} <ArrowRight size={18} aria-hidden="true" />
        </button>
        <p className="login-status" role="status">{status}</p>
      </form>}

      <footer className="login-footer">
        <Link className="login-link auth-back-link" to="/login"><ArrowLeft size={17} aria-hidden="true" /> Back to sign-in</Link>
      </footer>
    </LoginCard>
  )
}

export default function LoginPage() {
  const { user, sessionMessage, signIn, signOut, logoutPending } = useAuth()
  const [method, setMethod] = useState('password')
  const [remember, setRemember] = useState(false)
  const [enrolling, setEnrolling] = useState(false)
  const [ticket, setTicket] = useState(null)
  const [remaining, setRemaining] = useState(0)
  const otp = method === 'otp'
  const needPassword = !otp || (enrolling && !ticket)
  useEffect(() => {
    if (!ticket) return
    function update() { setRemaining(Math.max(0, Math.ceil((ticket.retry - Date.now()) / 1000))) }
    update()
    const timer = setInterval(update, 1000)
    return () => clearInterval(timer)
  }, [ticket])
  const { state } = useLocation()
  const navigate = useNavigate()
  const [role, setRole] = useState(state?.registeredEmail ? 'requester' : state?.portal || 'reviewer')
  const [showPassword, setShowPassword] = useState(false)
  const [errors, setErrors] = useState({})
  const [status, setStatus] = useState('')
  const [submittedRole, setSubmittedRole] = useState(null)
  const { busy, submit } = useAuthSubmission(setStatus)
  const emailInput = useRef(null)
  const credentialInput = useRef(null)
  const PasswordIcon = showPassword ? EyeOff : Eye
  const roleLabel = roles.find(({ id }) => id === role).label

  function resetFeedback() {
    setErrors({})
    setStatus('')
    setShowPassword(false)
  }

  function clearFieldError(field) {
    if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined }))
    if (status) setStatus('')
  }

  function validateAttempt() {
    const email = emailInput.current
    const credential = credentialInput.current
    const nextErrors = {}

    if (email.validity.valueMissing) nextErrors.email = 'Enter your email address.'
    else if (email.validity.typeMismatch) nextErrors.email = 'Enter a valid email address.'

    if (needPassword && credential?.validity.valueMissing) nextErrors.credential = 'Enter your password.'
    if (otp && ticket && !/^[0-9]{6}$/.test(credential?.value)) nextErrors.credential = 'Enter the six-digit email code.'

    setErrors(nextErrors)
    const invalidInput = nextErrors.email ? email : nextErrors.credential ? credential : null
    if (invalidInput) {
      setStatus('')
      invalidInput.focus()
      return false
    }
    return true
  }

  function handleSubmit(event) {
    event.preventDefault()
    if (busy || !validateAttempt()) return
    setSubmittedRole(role)
    const email = emailInput.current.value.trim()
    const value = credentialInput.current?.value
    if (!otp) return submit(async (signal) => {
      try {
        await signIn({ email, password: value, remember, portal: role }, signal)
      } catch (error) {
        if (error.status !== 428) throw error
        navigate('/forgot-password', { state: { email, portal: role } })
      }
    })
    submit(async (signal) => {
      if (!ticket) {
        const result = enrolling ? await authApi.enroll(email, value, role, signal) : await authApi.requestCode(email, signal)
        setTicket({ ...result, email, retry: Date.now() + 60000 })
        setStatus(enrolling ? 'Enter the code sent to your mailbox to enable email sign-in.' : 'If your account is enrolled, check your email for a code.')
      } else {
        const proof = { email, challenge: ticket.challenge, code: value }
        if (enrolling) {
          await authApi.confirm(ticket.token, proof, signal)
          setEnrolling(false)
          setTicket(null)
          setStatus('Email verified. Request a fresh sign-in code.')
        } else await signIn({ ...proof, remember, portal: role }, signal)
      }
    })
  }

  if (user) {
    const actualPortal = portals.find(({ id }) => id === user.role)
    if (submittedRole === user.role) return <Navigate replace to={`/${user.role}`} />
    return <LoginCard>
      <h1 id="login-title">Already signed in</h1>
      <p>Your current account has the {actualPortal.label} role.</p>
      <Link className="login-link" to={`/${user.role}`}>Continue to my portal</Link>
      <button className="login-link" type="button" onClick={signOut}>Sign out to use another account</button>
    </LoginCard>
  }

  const loginMessage = status ||
    (state?.passwordChanged ? 'Password set. Sign in with your new password.' : sessionMessage) ||
    (state?.registeredEmail ? 'Requester account created. Sign in to continue.' : '')

  return (
    <LoginCard>
      <div className="login-brand">
        <span className="brand-emblem" aria-hidden="true"><Wrench size={28} /></span>
        <div>
          <h1 id="login-title">FieldFix AI 2.0</h1>
          <p>Facility Maintenance Decision Support</p>
        </div>
      </div>

      <form className="login-form" method="post" onSubmit={handleSubmit} noValidate aria-busy={busy}>
        <SignInOptions name="sign-in-role" title="Sign-in role" options={roles} selected={role} disabled={busy} onChange={(value) => { setRole(value); setTicket(null); resetFeedback() }} />

        <SignInOptions name="sign-in-method" title="Sign-in Method" options={[{ id: 'password', label: 'Password' }, { id: 'otp', label: 'Email OTP' }]}
          selected={method} disabled={busy} onChange={(value) => { setMethod(value); setTicket(null); setEnrolling(false); resetFeedback() }} />
        <div className="login-field">
          <label htmlFor="sign-in-email">Email Address</label>
          <div className="dark-input" data-invalid={Boolean(errors.email)}>
            <Mail size={18} aria-hidden="true" />
            <input
              ref={emailInput}
              id="sign-in-email"
              type="email"
              autoComplete="username"
              placeholder="Enter your email address"
              defaultValue={state?.registeredEmail ?? ''}
              maxLength={320}
              required
              disabled={busy}
              aria-invalid={Boolean(errors.email)}
              aria-describedby={errors.email ? 'sign-in-email-error' : undefined}
              onChange={() => { setTicket(null); clearFieldError('email') }}
            />
          </div>
          {errors.email && <p className="login-field-error" id="sign-in-email-error" role="alert">{errors.email}</p>}
        </div>

        {needPassword && <div className="login-field" key="password">
          <label htmlFor="sign-in-password">{enrolling ? 'Confirm your account password' : 'Password'}</label>
          <div className="dark-input" data-invalid={Boolean(errors.credential)}>
            <LockKeyhole size={18} aria-hidden="true" />
            <input
              ref={credentialInput}
              id="sign-in-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              placeholder="Enter your password"
              maxLength={128}
              required
              disabled={busy}
              aria-invalid={Boolean(errors.credential)}
              aria-describedby={errors.credential ? 'sign-in-credential-error' : undefined}
              onChange={() => clearFieldError('credential')}
            />
            <button
              className="icon-action"
              type="button"
              disabled={busy}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              aria-controls="sign-in-password"
              onClick={() => setShowPassword((visible) => !visible)}
            >
              <PasswordIcon size={18} aria-hidden="true" />
            </button>
          </div>
          {errors.credential && <p className="login-field-error" id="sign-in-credential-error" role="alert">{errors.credential}</p>}
        </div>}
        {otp && ticket && <div className="login-field" key={ticket.challenge}>
          <label htmlFor="sign-in-code">Email verification code</label>
          <div className="dark-input"><KeyRound size={18} aria-hidden="true" />
            <input id="sign-in-code" ref={credentialInput} inputMode="numeric" autoComplete="one-time-code"
              maxLength={6} required disabled={busy} aria-invalid={Boolean(errors.credential)} onChange={() => clearFieldError('credential')} />
          </div>
          {errors.credential && <p className="login-field-error" role="alert">{errors.credential}</p>}
          <button className="login-link" type="button" disabled={busy || remaining > 0} onClick={() => {
            if (enrolling) { setTicket(null); return }
            submit(async (signal) => {
              const result = await authApi.requestCode(ticket.email, signal)
              setTicket({ ...result, email: ticket.email, retry: Date.now() + 60000 })
              setStatus('If your account is enrolled, check your email for a new code.')
            })
          }}>{remaining > 0 ? `Resend in ${remaining}s` : enrolling ? 'Restart verification' : 'Resend code'}</button>
        </div>}
        {!enrolling && <div className="login-remember"><label>
          <input type="checkbox" autoComplete="off" checked={remember} disabled={busy} onChange={(event) => setRemember(event.target.checked)} />
          <span>Keep me signed in on this device</span>
        </label></div>}
        <button className="primary-action" type="submit" disabled={busy} aria-describedby="sign-in-status">
          {busy ? 'Please wait…' : !otp ? `Sign In as ${roleLabel}` : !ticket ? 'Send OTP' : enrolling ? 'Verify email' : 'Verify & Sign In'} <ArrowRight size={18} aria-hidden="true" />
        </button>
        {otp && <button className="login-link" type="button" disabled={busy} onClick={() => {
          setEnrolling(!enrolling); setTicket(null); resetFeedback()
        }}>{enrolling ? 'Return to email sign-in' : 'Enable email sign-in with my password'}</button>}
        {logoutPending && <button type="button" className="login-link" onClick={signOut}>Retry sign-out</button>}
        <p id="sign-in-status" className="login-status" role="status">{loginMessage}</p>
      </form>

      <footer className="login-footer">
        <p>New user? <Link className="login-link" to="/register">Create a Requester Account</Link></p>
      </footer>
    </LoginCard>
  )
}
