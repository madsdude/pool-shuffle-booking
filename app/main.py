from __future__ import annotations

import os
from datetime import datetime, timedelta, time as time_cls
from typing import Optional, List, Dict

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import and_

# -------------------------------------------------------
# Robust get_db (prøv din egen, ellers fallback til DB_URL psycopg3)
# -------------------------------------------------------
def _resolve_get_db():
    try:
        from app.db import get_db as _get_db  # din dependency hvis den findes
        return _get_db
    except Exception:
        pass

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    DATABASE_URL = os.getenv(
        "DB_URL",
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
# ORM-modeller
# -------------------------------------------------------
from app.models import Booking, Resource

# Valgfri mail
try:
    from app.core.email import send_booking_confirmation, BookingEmailData
    MAIL_ENABLED = True
except Exception:
    MAIL_ENABLED = False

app = FastAPI(title="Pool & Shuffle Booking API")

# -------------------------------------------------------
# Pydantic schemas (v2)
# -------------------------------------------------------
class BookingCreate(BaseModel):
    resource_id: int
    date: str
    start_time: Optional[str] = None
    hour: Optional[int] = None
    duration: Optional[int] = 60
    name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None

    @field_validator("date")
    @classmethod
    def _d(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")
        return v

    @field_validator("start_time")
    @classmethod
    def _t(cls, v: Optional[str]) -> Optional[str]:
        if v:
            datetime.strptime(v, "%H:%M")
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
    class Config: from_attributes = True

# -------------------------------------------------------
# Hjælpere
# -------------------------------------------------------
def _parse_start(p: BookingCreate) -> time_cls:
    if p.start_time:
        return datetime.strptime(p.start_time, "%H:%M").time()
    if p.hour is not None:
        h = int(p.hour)
        if not (0 <= h <= 23): raise HTTPException(422, "hour skal være 0-23")
        return time_cls(h, 0)
    raise HTTPException(422, "Angiv enten start_time eller hour")

def _compose(local_date: str, start_t: time_cls, dur: int):
    s = datetime.combine(datetime.strptime(local_date, "%Y-%m-%d").date(), start_t)
    return s, s + timedelta(minutes=dur)

def _resource_name(db: Session, rid: int) -> str:
    r = db.query(Resource).filter(Resource.id == rid).first()
    return r.name if r else f"#{rid}"

def ensure_tables_and_seed(db: Session):
    """
    Sørger for at tabeller findes (checkfirst) og seed’er ressourcer hvis tomt.
    Kører sikkert flere gange.
    """
    # 1) create tables if missing
    try:
        # mest robuste måde uden at kende Base:
        Resource.__table__.create(bind=db.get_bind(), checkfirst=True)
        Booking.__table__.create(bind=db.get_bind(), checkfirst=True)
    except Exception:
        # hvis mapper ikke har __table__ (fx SQLModel), ignorer – eksisterende DDL håndterer det
        pass

    # 2) seed resources hvis tomt
    try:
        count = db.query(Resource).count()
    except Exception:
        return

    if count > 0:
        return

    pool_n = int(os.getenv("POOL_COUNT", "8"))
    shuffle_n = int(os.getenv("SHUFFLE_COUNT", "4"))

    items = []
    for i in range(1, pool_n + 1):
        r = Resource(name=f"Pool {i}")
        if hasattr(Resource, "kind"): setattr(r, "kind", "pool")
        items.append(r)
    for i in range(1, shuffle_n + 1):
        r = Resource(name=f"Shuffle {i}")
        if hasattr(Resource, "kind"): setattr(r, "kind", "shuffle")
        items.append(r)

    db.add_all(items)
    db.commit()

# -------------------------------------------------------
# Routes
# -------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/api/resources")
def resources(db: Session = Depends(get_db)):
    ensure_tables_and_seed(db)
    rows = db.query(Resource).order_by(Resource.id.asc()).all()
    return [{"id": r.id, "name": r.name, "kind": getattr(r, "kind", "pool")} for r in rows]

@app.get("/api/availability")
def availability(date: str, db: Session = Depends(get_db)):
    datetime.strptime(date, "%Y-%m-%d")  # valider
    d = datetime.strptime(date, "%Y-%m-%d").date()

    open_h = int(os.getenv("OPEN_HOUR", "15"))
    close_h = int(os.getenv("CLOSE_HOUR", "4"))
    open_dt = datetime.combine(d, time_cls(open_h, 0))
    close_dt = datetime.combine(d, time_cls(0, 0)) + timedelta(days=1, hours=close_h)

    slots, cur = [], open_dt
    while cur < close_dt:
        slots.append({"label": cur.strftime("%H:00"), "iso_start_local": cur.isoformat()})
        cur += timedelta(hours=1)

    rows = db.query(Resource).all()
    return {"open_local": open_dt.isoformat(), "close_local": close_dt.isoformat(),
            "resources": {r.id: slots for r in rows}}

@app.get("/api/bookings", response_model=List[BookingRead])
def list_bookings(date: Optional[str] = None, db: Session = Depends(get_db)):
    """Returnér bookinger (evt. filtreret på dato). Robust mod gamle/defekte rækker."""
    q = db.query(Booking)

    # filtrér væk rækker uden tider (kan ligge fra ældre versioner)
    q = q.filter(Booking.start.isnot(None), Booking.end.isnot(None))

    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="date skal være YYYY-MM-DD")
        start_day = datetime.combine(d, time_cls(0, 0))
        end_day = start_day + timedelta(days=1)
        q = q.filter(and_(Booking.start >= start_day, Booking.start < end_day))

    try:
        rows = q.order_by(Booking.start.asc()).all()
    except Exception as e:
        # Giv en pæn fejl der er nem at debugge, i stedet for en 500 uden indhold
        raise HTTPException(status_code=500, detail=f"DB error: {type(e).__name__}: {e}")

    out: List[BookingRead] = []
    for b in rows:
        try:
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
        except Exception:
            # spring defekte rækker over i stedet for at vælte hele svaret
            continue

    return out

@app.post("/api/bookings", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
def create_booking(p: BookingCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    s_t = _parse_start(p)
    dur = int(p.duration or 60)
    s, e = _compose(p.date, s_t, dur)

    overlap = (db.query(Booking)
               .filter(Booking.resource_id == p.resource_id)
               .filter(Booking.start < e).filter(Booking.end > s)).all()
    if overlap:
        raise HTTPException(409, "Tidsrummet er ikke ledigt")

    has_email_col = hasattr(Booking, "email")
    b = Booking(resource_id=p.resource_id, start=s, end=e, name=p.name, phone=p.phone,
                **({"email": p.email} if has_email_col and p.email else {}))
    db.add(b); db.commit(); db.refresh(b)

    if MAIL_ENABLED and p.email:
        try:
            mail = BookingEmailData(
                to=p.email, name=p.name, booking_id=str(b.id), date=p.date,
                start=s.strftime("%H:%M"), end=e.strftime("%H:%M"),
                table=_resource_name(db, p.resource_id),
                people=int(getattr(b, "people", 1)), phone=p.phone
            )
            background.add_task(send_booking_confirmation, mail)
        except Exception:
            pass

    return BookingRead(id=b.id, resource_id=b.resource_id, name=b.name, phone=b.phone,
                       email=(b.email if has_email_col else p.email),
                       start_iso_local=b.start.isoformat(), end_iso_local=b.end.isoformat())

@app.put("/api/bookings/{booking_id}", response_model=BookingRead)
def extend_booking(booking_id: int, p: BookingExtend, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b: raise HTTPException(404, "Booking ikke fundet")
    new_end = b.end + timedelta(minutes=int(p.add_minutes))
    overlap = (db.query(Booking)
               .filter(Booking.resource_id == b.resource_id, Booking.id != b.id)
               .filter(Booking.start < new_end, Booking.end > b.start)).all()
    if overlap: raise HTTPException(409, "Kan ikke forlænge – konflikt")
    b.end = new_end; db.add(b); db.commit(); db.refresh(b)
    return BookingRead(id=b.id, resource_id=b.resource_id, name=b.name, phone=b.phone,
                       email=getattr(b, "email", None),
                       start_iso_local=b.start.isoformat(), end_iso_local=b.end.isoformat())

@app.delete("/api/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b: raise HTTPException(404, "Booking ikke fundet")
    db.delete(b); db.commit(); return None

# ---------- HTML fallback (skader ikke Caddy) ----------
@app.get("/", include_in_schema=False)
def public_home():
    # Caddy server index.html/public-booking.html; dette er kun fallback
    return FileResponse("static/index.html")

@app.get("/staff", include_in_schema=False)
def staff_home():
    return FileResponse("static/staff.html")

