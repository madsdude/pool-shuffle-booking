from __future__ import annotations

import os
from datetime import datetime, timedelta, time as time_cls
from typing import Optional, List, Dict

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import and_

# --- Åbningsregler: KUN fredag/lørdag 19:00-23:00 ---
ALLOWED_DAYS = {4, 5}        # 0=man ... 4=fri, 5=lør
ALLOWED_START_HOUR = 19
ALLOWED_END_HOUR = 23

def _is_allowed_day(d: datetime.date) -> bool:
    return d.weekday() in ALLOWED_DAYS

def _is_within_allowed_window(start_dt: datetime, end_dt: datetime) -> bool:
    # Begge tider skal ligge på samme dato og indenfor [19:00, 23:00]
    if start_dt.date() != end_dt.date():
        return False
    if not _is_allowed_day(start_dt.date()):
        return False
    day_start = datetime.combine(start_dt.date(), time_cls(ALLOWED_START_HOUR, 0))
    day_end   = datetime.combine(start_dt.date(), time_cls(ALLOWED_END_HOUR, 0))
    return start_dt >= day_start and end_dt <= day_end


# ---- get_db fallback (virker også uden din egen app.db) ----
def _resolve_get_db():
    try:
        from app.db import get_db as _get_db
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

# ---- modeller ----
from app.models import Booking, Resource  # din models.py

# Valgfri mail
try:
    from app.core.email import send_booking_confirmation, BookingEmailData
    MAIL_ENABLED = True
except Exception:
    MAIL_ENABLED = False

app = FastAPI(title="Pool & Shuffle Booking API")

# ---------- kolonne-helpers (autodetect) ----------
def _col(model, candidates):
    for name in candidates:
        if hasattr(model, name):
            return getattr(model, name)
    raise RuntimeError(f"Mangler en af kolonnerne {candidates} på {model.__name__}")

BOOKING_START_COL = _col(Booking, ["start_utc", "start", "start_time", "start_dt", "starts_at", "begin"])
BOOKING_END_COL   = _col(Booking, ["end_utc",   "end",   "end_time",   "end_dt",   "ends_at",   "finish"])


# ---------- schemas ----------
class BookingCreate(BaseModel):
    resource_id: int
    date: str
    start_time: Optional[str] = None  # "HH:MM"
    hour: Optional[int] = None        # alternativ til start_time
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
    class Config:
        from_attributes = True

# ---------- utils ----------
def _parse_start(p: BookingCreate) -> time_cls:
    if p.start_time:
        return datetime.strptime(p.start_time, "%H:%M").time()
    if p.hour is not None:
        h = int(p.hour)
        if not (0 <= h <= 23):
            raise HTTPException(422, "hour skal være 0-23")
        return time_cls(h, 0)
    raise HTTPException(422, "Angiv enten start_time eller hour")

def _compose(local_date: str, start_t: time_cls, dur: int):
    s = datetime.combine(datetime.strptime(local_date, "%Y-%m-%d").date(), start_t)
    return s, s + timedelta(minutes=dur)

def _resource_name(db: Session, rid: int) -> str:
    r = db.query(Resource).filter(Resource.id == rid).first()
    return r.name if r else f"#{rid}"

def ensure_tables_and_seed(db: Session):
    # create tables if missing
    try:
        Resource.__table__.create(bind=db.get_bind(), checkfirst=True)
        Booking.__table__.create(bind=db.get_bind(), checkfirst=True)
    except Exception:
        pass
    # seed resources hvis tomt
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

# ---------- routes ----------
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
    d = datetime.strptime(date, "%Y-%m-%d").date()
    open_h = int(os.getenv("OPEN_HOUR", "15"))
    close_h = int(os.getenv("CLOSE_HOUR", "4"))  # næste dag
    open_dt = datetime.combine(d, time_cls(open_h, 0))
    close_dt = datetime.combine(d, time_cls(0, 0)) + timedelta(days=1, hours=close_h)

    slots, cur = [], open_dt
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
    q = db.query(Booking)
    q = q.filter(BOOKING_START_COL.isnot(None), BOOKING_END_COL.isnot(None))
    if date:
        d = datetime.strptime(date, "%Y-%m-%d").date()
        start_day = datetime.combine(d, time_cls(0, 0))
        end_day = start_day + timedelta(days=1)
        q = q.filter(and_(BOOKING_START_COL >= start_day, BOOKING_START_COL < end_day))
    rows = q.order_by(BOOKING_START_COL.asc()).all()

    out: List[BookingRead] = []
    for b in rows:
        try:
            s = getattr(b, BOOKING_START_COL.key)
            e = getattr(b, BOOKING_END_COL.key)
            out.append(
                BookingRead(
                    id=b.id,
                    resource_id=b.resource_id,
                    name=b.name,
                    phone=b.phone,
                    email=getattr(b, "email", None),
                    start_iso_local=s.isoformat(),
                    end_iso_local=e.isoformat(),
                )
            )
        except Exception:
            continue
    return out

@app.post("/api/bookings", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
def create_booking(p: BookingCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    start_t = _parse_start(p)
    dur = int(p.duration or 60)
    s_dt, e_dt = _compose(p.date, start_t, dur)

    # konflikt
    overlap = (
        db.query(Booking)
        .filter(Booking.resource_id == p.resource_id)
        .filter(BOOKING_START_COL < e_dt)
        .filter(BOOKING_END_COL > s_dt)
        .all()
    )
    if overlap:
        raise HTTPException(409, "Tidsrummet er ikke ledigt")

    has_email_col = hasattr(Booking, "email")
    b = Booking(
        resource_id=p.resource_id,
        name=p.name,
        phone=p.phone,
        **({"email": p.email} if has_email_col and p.email else {})
    )
    setattr(b, BOOKING_START_COL.key, s_dt)
    setattr(b, BOOKING_END_COL.key, e_dt)

    db.add(b)
    db.commit()
    db.refresh(b)

    if MAIL_ENABLED and p.email:
        try:
            mail = BookingEmailData(
                to=p.email, name=p.name, booking_id=str(b.id), date=p.date,
                start=s_dt.strftime("%H:%M"), end=e_dt.strftime("%H:%M"),
                table=_resource_name(db, p.resource_id),
                people=int(getattr(b, "people", 1)), phone=p.phone
            )
            background.add_task(send_booking_confirmation, mail)
        except Exception:
            pass

    return BookingRead(
        id=b.id,
        resource_id=b.resource_id,
        name=b.name,
        phone=b.phone,
        email=(b.email if has_email_col else p.email),
        start_iso_local=s_dt.isoformat(),
        end_iso_local=e_dt.isoformat(),
    )

@app.put("/api/bookings/{booking_id}", response_model=BookingRead)
def extend_booking(booking_id: int, p: BookingExtend, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b:
        raise HTTPException(404, "Booking ikke fundet")

    cur_end = getattr(b, BOOKING_END_COL.key)
    new_end = cur_end + timedelta(minutes=int(p.add_minutes))

    overlap = (
        db.query(Booking)
        .filter(Booking.resource_id == b.resource_id, Booking.id != b.id)
        .filter(BOOKING_START_COL < new_end, BOOKING_END_COL > getattr(b, BOOKING_START_COL.key))
        .all()
    )
    if overlap:
        raise HTTPException(409, "Kan ikke forlænge – konflikt")

    setattr(b, BOOKING_END_COL.key, new_end)
    db.add(b); db.commit(); db.refresh(b)

    return BookingRead(
        id=b.id, resource_id=b.resource_id, name=b.name, phone=b.phone,
        email=getattr(b, "email", None),
        start_iso_local=getattr(b, BOOKING_START_COL.key).isoformat(),
        end_iso_local=getattr(b, BOOKING_END_COL.key).isoformat(),
    )

@app.delete("/api/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if not b: raise HTTPException(404, "Booking ikke fundet")
    db.delete(b); db.commit(); return None

# ---------- HTML fallback (skader ikke Caddy) ----------
@app.get("/", include_in_schema=False)
def public_home():
    return FileResponse("static/index.html")

@app.get("/staff", include_in_schema=False)
def staff_home():
    return FileResponse("static/staff.html")


