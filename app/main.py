from __future__ import annotations

from datetime import datetime, timedelta, time as time_cls
from typing import Optional, List, Dict

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

# ---- Importér DB dependency og modeller (med fleksible stier) ----
# get_db()
try:
    from app.db import get_db
except Exception:
    from app.database import get_db  # fallback hvis din dependency ligger her

# Booking, Resource (SQLAlchemy / SQLModel ORM-klasser)
from app.models import Booking, Resource  # denne findes i dit repo

# Mail helper fra Fil 1
from app.core.email import send_booking_confirmation, BookingEmailData

app = FastAPI(title="Pool & Shuffle Booking API")

# ------------------------------------------------------------------
#                 SCHEMAS (Pydantic v2)
# ------------------------------------------------------------------
class BookingCreate(BaseModel):
    resource_id: int
    date: str                     # "YYYY-MM-DD"
    start_time: Optional[str] = None  # "HH:MM" (alternativ til hour)
    hour: Optional[int] = None        # heletimeslot (0-23)
    duration: Optional[int] = 60      # minutter (default 60)
    name: str
    phone: Optional[str] = None
    email: EmailStr                 # påkrævet for bekræftelsesmail

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


# ------------------------------------------------------------------
#                              ROUTES
# ------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/api/resources")
def resources(db: Session = Depends(get_db)):
    """Returnér alle ressourcer (pool/shuffle)."""
    rows = db.query(Resource).order_by(Resource.id.asc()).all()
    # returneres som simple dicts (id, name, kind)
    return [{"id": r.id, "name": r.name, "kind": getattr(r, "kind", "pool")} for r in rows]

@app.get("/api/availability")
def availability(date: str, db: Session = Depends(get_db)):
    """
    Returnér tilgængelige timeslots pr. resource for en given dato.
    Klient bruger kun 'label' (HH:00) til at forfylde start.
    """
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date skal være YYYY-MM-DD")

    # Åbne/lukke tider (kan evt. læses fra env, her hardcodet 15–04 som i compose)
    open_h = 15
    close_h = 4  # næste dag
    open_dt = datetime.combine(d, time_cls(open_h, 0))
    close_dt = datetime.combine(d, time_cls(0, 0)) + timedelta(days=1, hours=close_h)

    # Byg slots som hele timer fra open_dt til close_dt
    slots = []
    cur = open_dt
    while cur < close_dt:
        slots.append({"label": cur.strftime("%H:00"), "iso_start_local": cur.isoformat()})
        cur += timedelta(hours=1)

    # samme slots for alle resources (klienten viser occupancy på bogførte bookinger)
    rows = db.query(Resource).all()
    return {
        "open_local": open_dt.isoformat(),
        "close_local": close_dt.isoformat(),
        "resources": {r.id: slots for r in rows}
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
    Opret booking + send bekræftelsesmail i baggrunden.
    Kræver 'email' i payload.
    """
    # 1) tider
    start_t = _parse_start(payload)
    duration = int(payload.duration or 60)
    start_dt, end_dt = _compose_datetimes(payload.date, start_t, duration)

    # 2) konfliktcheck
    overlaps = (
        db.query(Booking)
        .filter(Booking.resource_id == payload.resource_id)
        .filter(Booking.start < end_dt)
        .filter(Booking.end > start_dt)
        .all()
    )
    if overlaps:
        raise HTTPException(status_code=409, detail="Tidsrummet er ikke ledigt")

    # 3) gem
    # Hvis din DB-model ikke har kolonnen 'email', fjern 'email=...' fra initialisering.
    has_email_col = hasattr(Booking, "email")
    b = Booking(
        resource_id=payload.resource_id,
        start=start_dt,
        end=end_dt,
        name=payload.name,
        phone=payload.phone,
        **({"email": payload.email} if has_email_col else {})
    )
    db.add(b)
    db.commit()
    db.refresh(b)

    # 4) mail
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
        # Fejl i mail må ikke blokere selve bookingen
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
