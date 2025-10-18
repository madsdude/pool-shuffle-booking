import os
import asyncio
from pydantic import BaseModel, EmailStr
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig

class BookingEmailData(BaseModel):
    to: EmailStr
    name: str
    booking_id: str
    date: str
    start: str
    end: str
    table: str
    people: int = 1
    phone: str | None = None

def _bool(v: str | None, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

_CONF: ConnectionConfig | None = None

def _get_conf() -> ConnectionConfig | None:
    """Bygger og cacher SMTP-konfiguration fra miljøvariabler.
       Returnerer None hvis noget mangler -> sender ikke mails men crasher heller ikke.
    """
    global _CONF
    if _CONF is not None:
        return _CONF

    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    port = int(os.getenv("SMTP_PORT", "587"))
    email_from = os.getenv("EMAIL_FROM")

    if not host or not user or not pwd or not email_from:
        print("[mail] Missing SMTP env (SMTP_HOST/USER/PASS or EMAIL_FROM). Emails disabled.")
        _CONF = None
        return None

    use_tls = _bool(os.getenv("SMTP_TLS", "true"), True)
    # STARTTLS for 587, SSL/TLS for 465 — ellers almindelig clear (ikke anbefalet)
    starttls = use_tls and port != 465
    ssl_tls  = use_tls and port == 465

    _CONF = ConnectionConfig(
        MAIL_USERNAME=user,
        MAIL_PASSWORD=pwd,
        MAIL_FROM=email_from,
        MAIL_FROM_NAME="Poolhall Booking",
        MAIL_PORT=port,
        MAIL_SERVER=host,
        MAIL_STARTTLS=starttls,
        MAIL_SSL_TLS=ssl_tls,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
        SUPPRESS_SEND=False,
        TEMPLATE_FOLDER=None,
    )
    return _CONF

def _build_html(data: BookingEmailData) -> str:
    phone_li = f"<li><b>Telefon:</b> {data.phone}</li>" if data.phone else ""
    return f"""<!doctype html>
<html>
  <body style="font-family:system-ui,Segoe UI,Arial,sans-serif;color:#111">
    <h2>Tak for din booking, {data.name}!</h2>
    <p>Her er din bekræftelse:</p>
    <ul>
      <li><b>Booking-id:</b> {data.booking_id}</li>
      <li><b>Dato:</b> {data.date}</li>
      <li><b>Tid:</b> {data.start} – {data.end}</li>
      <li><b>Bord:</b> {data.table}</li>
      <li><b>Antal personer:</b> {data.people}</li>
      {phone_li}
    </ul>
    <p>Hvis du har spørgsmål, svar på denne mail.</p>
    <p>– Poolhall</p>
  </body>
</html>"""

async def _send_async(data: BookingEmailData):
    conf = _get_conf()
    if conf is None:
        return  # ikke konfigureret -> gør ingenting
    fm = FastMail(conf)
    subject = f"Bekræftelse – {data.date} {data.start}-{data.end} – {data.table}"
    msg = MessageSchema(
        subject=subject,
        recipients=[data.to],
        body=_build_html(data),
        subtype="html",
    )
    await fm.send_message(msg)
    print(f"[mail] Sent booking confirmation to {data.to}")

def send_booking_confirmation(data: BookingEmailData):
    """Synkron wrapper (kan bruges i BackgroundTasks)."""
    try:
        asyncio.run(_send_async(data))
    except RuntimeError:
        # Hvis der mod forventning er en event loop i gang:
        loop = asyncio.get_event_loop()
        loop.create_task(_send_async(data))
