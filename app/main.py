from __future__ import annotations

import os
from datetime import datetime, timedelta, time as time_cls
from typing import Optional, List, Dict

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

# -------------------------------------------------------
#  get_db: robust import + fallback (psycopg3)
# -------------------------------------------------------
def _resolve_get_db():
    # 1) Prøv din eksisterende dependency (typisk app.db.get_db)
    try:
        from app.db import get_db as _get_db  # type: ignore
        return _get_db
    except Exception:
        pass

    # 2) Fallback: SessionLocal ud fra env DB_URL / psycopg3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    DATABASE_URL = os.getenv(
        "DB_URL",  # brug denne hvis sat
        os.getenv("DATABASE_URL", "postgresql+psycopg://booking:booking@db:5432/booking")
    )
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    return _get_db

get_db = _resolve_get_db()

# -------------------------------------------------------
#  ORM-modeller (findes i dit repo)
# -------------------------------------------------------
from app.models import Booking, Resource

# -------------------------------------------------------
#  (Valgfrit) Mail – brug hvis du har app/core/email.py
# -------------------------------------------------------
try:
    from app.core.email import send_booking_confirmation, BookingEmailData  # type: ignore
    MAIL_ENABLED = True
except Exception:
    MAIL_ENABLED = False


app = FastAPI(title="Pool & Shuffle Booking API")


# ------------------------------------------------------------------
#                 SCHEMAS (Pydantic v2)
# ------------------------------------------------------------------
class BookingCreate(BaseModel):
    resource_id: int
    date: str                     # "YYYY-MM-DD"
    start_time: Optional[str] = None  # "HH:MM" (alternativ til hour)
    hour: Optional[int] = None        # heletime (0-23)
    duration: Optional[int] = 60      # minutter (default 60)
    name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None  # kun nødvendig til public bekræftelsesmail

    @field_validator("date")
    @classmethod
    def _validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("date skal være YYYY-MM-DD")
        return v

    @field_validator("start_time")
    @classmethod
    def _validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError("start_time skal være HH:MM")
        return v


class BookingExtend(BaseModel):
    add_minutes: int


class BookingRead(BaseModel):
    id: int
    resource_id: int
    name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    start_iso_local: str
    end_iso_local: str

    class Config:
        from_attributes = True


# ------------------------------------------------------------------
#                       HJÆLPEFUNKTIONER
# ------------------------------------------------------------------
def _parse_start(payload: BookingCreate) -> time_cls:
    """Returnér start som time:minut. Bruger start_time hvis givet, ellers hour."""
    if payload.start_time:
        return datetime.strptime(payload.start_time, "%H:%M").time()
    if payload.hour is not None:
        h = int(payload.hour)
        if not (0 <= h <= 23):
            raise HTTPException(status_code=422, detail="hour skal være 0-23")
        return time_cls(hour=h, minute=0)
    raise HTTPException(status_code=422, detail="Angiv enten start_time eller hour")

def _compose_datetimes(local_date: str, start_t: time_cls, duration_min: int) -> tuple[datetime, datetime]:
    """Lav naive lokale datotider ud fra dato + start + varighed."""
    start_dt = datetime.combine(datetime.strptime(local_date, "%Y-%m-%d").date(), start_t)
    end_dt = start_dt + timedelta(minutes=duration_min)
    return start_dt, end_dt

def _resource_name(db: Session, resource_id: int) -> str:
    r = db.query(Resource).filter(Resource.id == resource_id).first()
    return r.name if r else f"#{resource_id}"

def ensure_default_resources(db: Session):
    """Opret standardborde hvis tabellen er tom. Styres af POOL_COUNT/SHUFFLE_COUNT (env)."""
    try:
        count = db.query(Resource).count()
    except Exception:
        return  # tabel ikke klar endnu (DDL/migration håndterer det)

    if count > 0:
        return

    pool_n = int(os.getenv("POOL_COUNT", "8"))
    shuffle_n = int(os.getenv("SHUFFLE_COUNT", "4"))

    items = []
    for i in range(1, pool_n + 1):
        r = Resource(name=f"Pool {i}")
        if hasattr(Resource, "kind"):
            setattr(r, "kind", "pool")
        items.append(r)

    for i in range(1, shuffle_n + 1):
        r = Resource(name=f"Shuffle {i}")
        if hasattr(Resource, "kind"):
            setattr(r, "kind", "shuffle")
        items.append(r)

    db.add_all(items)
    db.commit()


# ------------------------------------------------------------------
#                              ROUTES
# ------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/resources")
def resources(db: Session = Depends(get_db)):
    """Returnér alle ressourcer (pool/shuffle). Seeder automatisk hvis tom."""
    ensure_default_resources(db)
    rows = db.query(Resource).order_by(Resource.id.asc()).all()
    return [{"id": r.id, "name": r.name, "kind": getattr(r, "kind", "pool")} for r in rows]


@app.get("/api/availability")
def availability(date: str, db: Session = Depends(get_db)):
    """
    Returnér tilgængelige timeslots pr. resource for en given dato.
    """
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date skal være YYYY-MM-DD")

    # Åbne/lukke tider (kan læses fra env; default 15–04)
    open_h = int(os.getenv("OPEN_HOUR", "15"))
    close_h = int(os.getenv("CLOSE_HOUR", "4"))  # næste dag
    open_dt = datetime.combine(d, time_cls(open_h, 0))
    close_dt = datetime.combine(d, time_cls(0, 0)) + timedelta(days=1, hours=close_h)

    slots = []
    cur = open_dt
    while cur < close_dt:
        slots.append({"label": cur.strftime("%H:00"), "iso_start_local": cur.isoformat()})
        cur += timedelta(hours=1)

    rows = db.query(Resource).all()
    return {
        "open_local": open_dt.isoformat(),
        "close_local": close_dt.isoformat(),
        "resources": {r.id: slots for r in rows},
    }


@app.get("/api/bookings", response_model=List[BookingRead])
def list_bookings(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Returnér bookinger (evt. filtreret på dato)."""
    q = db.query(Booking)
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="date skal være YYYY-MM-DD")
        start_day = datetime.combine(d, time_cls(0, 0))
        end_day = start_day + timedelta(days=1)
        q = q.filter(Booking.start >= start_day, Booking.start < end_day)

    rows = q.order_by(Booking.start.asc()).all()
    out: List[BookingRead] = []
    for b in rows:
        out.append(
            BookingRead(
                id=b.id,
                resource_id=b.resource_id,
                name=b.name,
                phone=b.phone,
                email=getattr(b, "email", None),
                start_iso_local=b.start.isoformat(),
                end_iso_local=b.end.isoformat(),
            )
        )
    return out


@app.post("/api/bookings", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
def create_booking(payload: BookingCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Opret booking. Hvis 'email' gives og MAIL_ENABLED=True, sendes bekræftelsesmail i baggrunden.
    """
    start_t = _parse_start(payload)
    duration = int(payload.duration or 60)
    start_dt, end_dt = _compose_datetimes(payload.date, start_t, duration)

    # konfliktcheck
    overlaps = (
        db.query(Booking)
        .filter(Booking.resource_id == payload.resource_id)
        .filter(Booking.start < end_dt)
        .filter(Booking.end > start_dt)
        .all()
    )
    if overlaps:
        raise HTTPException(status_code=409, detail="Tidsrummet er ikke ledigt")

    has_email_col = hasattr(Booking, "email")
    b = Booking(
        resource_id=payload.resource_id,
        start=start_dt,
        end=end_dt,
        name=payload.name,
        phone=payload.phone,
        **({"email": payload.email} if has_email_col and payload.email else {})
    )
    db.add(b)
    db.commit()
    db.refresh(b)

    # bekræftelsesmail (valgfrit)
    if MAIL_ENABLED and payload.email:
        try:
            mail = BookingEmailData(
                to=payload.email,
                name=payload.name,
                booking_id=str(b.id),
                date=payload.date,
                start=start_dt.strftime("%H:%M"),
                end=end_dt.strftime("%H:%M"),
                table=_resource_name(db, payload.resource_id),
                people=int(getattr(b, "people", 1)),
                phone=payload.phone,
            )
            background.add_task(send_booking_confirmation, mail)
        except Exception:
            # fejl i mail må ikke blokere bookingen
            pass

    return BookingRead(
        id=b.id,
        resource_id=b.resource_id,
        name=b.name,
        phone=b.phone,
        email=(b.email if has_email_col else payload.email),
        start_iso_local=b.start.isoformat(),
        end_iso_local=b.end.isoformat(),
    )


@app.put("/api/bookings/{booking_id}", response_model=BookingRead)
def extend_booking(booking_id: int, payload: BookingExtend, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking ikke fundet")

    new_end = b.end + timedelta(minutes=int(payload.add_minutes))

    overlaps = (
        db.query(Booking)
        .filter(Booking.resource_id == b.resource_id)
        .filter(Booking.id != b.id)
        .filter(Booking.start < new_end)
        .filter(Booking.end > b.start)
        .all()
    )
    if overlaps:
        raise HTTPException(status_code=409, detail="Kan ikke forlænge – konflikt")

    b.end = new_end
    db.add(b)
    db.commit()
    db.refresh(b)

    return BookingRead(
        id=b.id,
        resource_id=b.resource_id,
        name=b.name,
        phone=b.phone,
        email=getattr(b, "email", None),
        start_iso_local=b.start.isoformat(),
        end_iso_local=b.end.isoformat(),
    )


@app.delete("/api/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking ikke fundet")
    db.delete(b)
    db.commit()
    return None


# ---------- HTML-forsider (kun relevant hvis Caddy ikke serverer dem) ----------
@app.get("/", include_in_schema=False)
def public_home():
    # brug din faktiske public fil; Caddy peger typisk selv på denne
    # return FileResponse("static/public-booking.html")
    return FileResponse("static/index.html")

@app.get("/staff", include_in_schema=False)
def staff_home():
    return FileResponse("static/staff.html")
