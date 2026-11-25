"""Five-minute challenges. The delivery adapter never receives database credentials."""
import os
import secrets
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session, select

from app import browser_sessions as sessions
from app.models import User


class SmtpMailer:
    def __init__(self):
        self.host = os.environ.get("FIELDFIX_SMTP_HOST")
        self.username = os.environ.get("FIELDFIX_SMTP_USER")
        self.password = os.environ.get("FIELDFIX_SMTP_PASSWORD")
        try:
            self.sender = TypeAdapter(EmailStr).validate_python(os.environ.get("FIELDFIX_SMTP_FROM"))
            self.port = int(os.environ.get("FIELDFIX_SMTP_PORT", "465"))
            if not (self.host and self.username and self.password and 0 < self.port <= 65535):
                raise ValueError
        except (ValidationError, ValueError):
            raise HTTPException(503, "Email authentication is currently unavailable.") from None

    def send(self, email, code, purpose):
        message = EmailMessage()
        message["From"], message["To"] = self.sender, email
        action = "enrollment" if purpose == "enroll" else "sign-in"
        message["Subject"] = f"FieldFix email {action} code"
        message.set_content(f"Your FieldFix {action} code is {code}. It expires in five minutes.")
        with smtplib.SMTP_SSL(self.host, self.port, timeout=8, context=ssl.create_default_context()) as smtp:
            smtp.login(self.username, self.password)
            smtp.send_message(message)


def get_mailer():
    return SmtpMailer()


def limit(db, key, scope, identifier, stamp, seconds, maximum):
    bucket = sessions.digest(key, scope, identifier)
    row = sessions.query(db, "SELECT since,hits FROM auth_limit WHERE bucket=:b", b=bucket).first()
    if row and row.since + seconds > stamp:
        if row.hits >= maximum:
            return False
        sessions.query(db, "UPDATE auth_limit SET hits=hits+1 WHERE bucket=:b", b=bucket)
    else:
        sessions.query(db, """INSERT OR REPLACE INTO auth_limit(bucket,since,hits) VALUES (:b,:t,1)""",
                       b=bucket, t=stamp)
    return True


def throttles(db, key, action, email, request):
    stamp = sessions.now()
    source = request.client.host if request.client else "unknown"
    sending = action == "send"
    # Check the source first to bound unknown-address writes from a single source.
    ok = limit(db, key, action+" source", source, stamp, 3600, 40 if sending else 120)
    if ok:
        ok = limit(db, key, action+" account", email, stamp, 3600, 6 if sending else 25)
    if ok and sending:
        ok = limit(db, key, "cooldown", email, stamp, 60, 1)
    if not ok:
        db.commit()
        raise HTTPException(429, "Please wait before trying again.")


def mail_after_commit(engine, mailer, email, code, purpose, challenge):
    try:
        mailer.send(email, code, purpose)
    except Exception:
        # Any delivery error invalidates only this challenge. Never log the
        # exception: an SMTP server may echo the address or message content.
        with Session(engine) as db:
            sessions.query(db, "DELETE FROM email_challenge WHERE id=:id", id=challenge)
            db.commit()


def issue(db, request, email, purpose, key, mailer, tasks, password_user=None):
    sessions.guard(request)
    if password_user is None:
        sessions.write_lock(db)
    stamp = sessions.now()
    sessions.cleanup(db, stamp)
    throttles(db, key, "send", email, request)
    user = db.exec(select(User).where(User.email == email)).first()
    challenge = secrets.token_urlsafe(24)
    verified = sessions.query(db, "SELECT 1 FROM verified_mailbox WHERE user_id=:u AND email=:e",
                              u=user.id if user else None, e=email).first()
    allowed = user and user.is_active and (verified if purpose == "login" else password_user == user.id)
    if allowed:
        code = f"{secrets.randbelow(1000000):06d}"
        protected = sessions.digest(key, "email otp", challenge+code)
        sessions.query(db, """INSERT OR REPLACE INTO email_challenge(id,user_id,purpose,email,digest,expires)
                       VALUES(:id,:u,:p,:e,:d,:x)""", id=challenge, u=user.id, p=purpose, e=email,
                       d=protected, x=stamp+300)
        tasks.add_task(mail_after_commit, db.get_bind(), mailer, email, code, purpose, challenge)
    db.commit()
    return {"challenge": challenge, "retry_after": 60}


def consume(db, request, body, purpose, key, required_user=None):
    sessions.guard(request)
    sessions.write_lock(db)
    throttles(db, key, "verify", body.email, request)
    row = sessions.query(db, """SELECT * FROM email_challenge WHERE id=:c AND email=:e AND purpose=:p""",
                         c=body.challenge, e=body.email, p=purpose).mappings().first()
    if row and row["expires"] > sessions.now() and row["attempts"] < 5:
        protected = sessions.digest(key, "email otp", body.challenge + body.code.get_secret_value())
        user = db.get(User, row["user_id"])
        if (secrets.compare_digest(row["digest"], protected) and user and user.is_active
                and user.email == body.email and (required_user is None or user.id == required_user)):
            sessions.query(db, "DELETE FROM email_challenge WHERE id=:c", c=body.challenge)
            return user.id
        sessions.query(db, "UPDATE email_challenge SET attempts=attempts+1 WHERE id=:c", c=body.challenge)
    db.commit()
    raise HTTPException(401, "Invalid or expired verification code.")
