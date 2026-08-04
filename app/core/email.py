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
    phone_row = f"""
      <tr>
        <td style="padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9;">Telefon</td>
        <td style="padding: 10px 14px; color: #0f172a; font-weight: 500; font-size: 14px; border-bottom: 1px solid #f1f5f9; text-align: right;">{data.phone}</td>
      </tr>
    """ if data.phone else ""

    return f"""<!doctype html>
<html lang="da">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bookingbekræftelse – Pool Hall Randers</title>
  </head>
  <body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0f172a; padding: 30px 15px;">
      <tr>
        <td align="center">
          <!-- Main Container Card -->
          <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);">
            
            <!-- Header with Neon/Orange Branding -->
            <tr>
              <td style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 32px 24px; text-align: center; border-bottom: 3px solid #f97316;">
                <div style="display: inline-block; background-color: #f97316; color: #ffffff; font-weight: 800; font-size: 12px; letter-spacing: 2px; text-transform: uppercase; padding: 4px 12px; border-radius: 20px; margin-bottom: 12px;">
                  POOL HALL RANDERS
                </div>
                <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; line-height: 1.3;">
                  Bookingbekræftelse 🎯
                </h1>
              </td>
            </tr>

            <!-- Content Area -->
            <tr>
              <td style="padding: 32px 28px;">
                <h2 style="color: #0f172a; font-size: 18px; margin-top: 0; margin-bottom: 8px;">
                  Tak for din booking, {data.name}!
                </h2>
                <p style="color: #475569; font-size: 15px; line-height: 1.6; margin-top: 0; margin-bottom: 24px;">
                  Vi har modtaget din reservation og glæder os til at tage imod dig hos Pool Hall Randers. Her er dine bookingoplysninger:
                </p>

                <!-- Details Table -->
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; border-collapse: separate; overflow: hidden; margin-bottom: 28px;">
                  <tr>
                    <td style="padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9;">Booking ID</td>
                    <td style="padding: 10px 14px; color: #f97316; font-weight: 700; font-size: 14px; border-bottom: 1px solid #f1f5f9; text-align: right;">#{data.booking_id}</td>
                  </tr>
                  <tr>
                    <td style="padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9;">Dato</td>
                    <td style="padding: 10px 14px; color: #0f172a; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9; text-align: right;">{data.date}</td>
                  </tr>
                  <tr>
                    <td style="padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9;">Tidspunkt</td>
                    <td style="padding: 10px 14px; color: #0f172a; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9; text-align: right;">{data.start} – {data.end}</td>
                  </tr>
                  <tr>
                    <td style="padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9;">Bord / Aktivitet</td>
                    <td style="padding: 10px 14px; color: #0f172a; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9; text-align: right;">{data.table}</td>
                  </tr>
                  <tr>
                    <td style="padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9;">Antal personer</td>
                    <td style="padding: 10px 14px; color: #0f172a; font-weight: 600; font-size: 14px; border-bottom: 1px solid #f1f5f9; text-align: right;">{data.people}</td>
                  </tr>
                  {phone_row}
                </table>

                <!-- Facebook Contact Box -->
                <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px;">
                  <h3 style="color: #166534; font-size: 15px; margin: 0 0 6px 0; font-weight: 700;">
                    Brug for hjælp eller spørgsmål?
                  </h3>
                  <p style="color: #15803d; font-size: 13.5px; line-height: 1.5; margin: 0 0 14px 0;">
                    Hvis du har spørgsmål eller ændringer til din booking, bedes du venligst kontakte os direkte via vores Facebook-side. Bemærk at du ikke kan besvare denne e-mail.
                  </p>
                  <a href="https://www.facebook.com/alpetoppenranders" target="_blank" style="display: inline-block; background-color: #1877f2; color: #ffffff; font-weight: 600; font-size: 14px; padding: 10px 20px; border-radius: 8px; text-decoration: none; box-shadow: 0 2px 4px rgba(24, 119, 242, 0.25);">
                    Kontakt os på Facebook
                  </a>
                </div>

                <p style="color: #64748b; font-size: 14px; margin: 0; text-align: center;">
                  Vi glæder os til en fantastisk aften i selskab med dig! 🍻
                </p>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="background-color: #f8fafc; padding: 20px 28px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="color: #94a3b8; font-size: 12px; margin: 0; line-height: 1.5;">
                  <strong>Pool Hall Randers</strong><br>
                  Din foretrukne bar til pool, shuffleboard &amp; kolde øl.
                </p>
              </td>
            </tr>

          </table>
        </td>
      </tr>
    </table>
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
