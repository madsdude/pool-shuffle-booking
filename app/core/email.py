# app/core/email.py
from __future__ import annotations
import os
from typing import Optional
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pydantic import BaseModel, EmailStr

# Læses fra environment (.env / docker-compose)
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("SMTP_USER"),
    MAIL_PASSWORD=os.getenv("SMTP_PASS"),
    MAIL_FROM=os.getenv("EMAIL_FROM", "noreply@example.com"),
    MAIL_PORT=int(os.getenv("SMTP_PORT", "587")),
    MAIL_SERVER=os.getenv("SMTP_HOST", "localhost"),
    MAIL_STARTTLS=(os.getenv("SMTP_TLS", "true").lower() == "true"),
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
)

fm = FastMail(conf)

class BookingEmailData(BaseModel):
    to: EmailStr
    name: str
    booking_id: str
    date: str
    start: str
    end: str
    table: str
    people: int
    phone: Optional[str] = None

EMAIL_TEMPLATE_HTML = """
<div style="font-family:Arial,sans-serif;line-height:1.45">
  <h2 style="margin:0 0 12px 0">Tak for din booking, {name}!</h2>
  <p style="margin:0 0 12px 0">Vi glæder os til at se dig.</p>

  <table style="border-collapse:collapse">
    <tr><td style="padding:4px 8px"><b>Booking #</b></td><td style="padding:4px 8px">{booking_id}</td></tr>
    <tr><td style="padding:4px 8px"><b>Dato</b></td><td style="padding:4px 8px">{date}</td></tr>
    <tr><td style="padding:4px 8px"><b>Tid</b></td><td style="padding:4px 8px">{start} – {end}</td></tr>
    <tr><td style="padding:4px 8px"><b>Bord</b></td><td style="padding:4px 8px">{table}</td></tr>
    <tr><td style="padding:4px 8px"><b>Personer</b></td><td style="padding:4px 8px">{people}</td></tr>
  </table>

  <p style="margin:16px 0 0 0">Har du spørgsmål? Svar på denne mail.</p>
  <p style="margin:8px 0 0 0">— Pool Hall Randers</p>
</div>
""".strip()

async def send_booking_confirmation(data: BookingEmailData):
    """
    Sender en HTML bekræftelsesmail til gæsten.
    Kaster exception hvis SMTP fejler (så fang den i background task med logging i prod).
    """
    html_body = EMAIL_TEMPLATE_HTML.format(**data.model_dump())
    message = MessageSchema(
        subject=f"Bekræftelse på booking #{data.booking_id}",
        recipients=[data.to],
        body=html_body,
        subtype="html",
    )
    await fm.send_message(message)
