from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import SessionLocal, init_db, Resource, Booking

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")
OPEN_HOUR = int(os.environ.get("OPEN_HOUR", 10))
# 24 betyder “næste dags 00:00”. Hvis CLOSE_HOUR < OPEN_HOUR, tolkes som luk næste dag (fx 04).
CLOSE_HOUR = int(os.environ.get("CLOSE_HOUR", 24))
UTC = ZoneInfo("UTC")

def day_hours(day_local: datetime) -> tuple[int, int]:
    """
    Returnér (open_hour, close_hour) for den konkrete dag.
    Standard er OPEN_HOUR/CLOSE_HOUR, men:
      - Fredag (4) og Lørdag (5) åbner 15:00 og lukker kl. 02 (26)
    """
    wd = day_local.weekday()  # 0=man ... 6=søn
    # defaults fra env/globaler (tidligere i filen)
    oh = OPEN_HOUR
    ch = CLOSE_HOUR
    if wd == 4:  # fredag
        oh, ch = 15, 26
    elif wd == 5:  # lørdag
        oh, ch = 15, 26
    # (valgfrit: sæt søndag til 01 -> ch = 25)
    return oh, ch

def business_window(day_local: datetime):
    """
    Returnerer (open_dt, close_dt) for en given dag.
    Håndterer:
      - CLOSE_HOUR == 24   -> næste dag kl. 00:00
      - CLOSE_HOUR < OPEN_HOUR -> luk næste dag (fx åbner 10, lukker 04)
      - CLOSE_HOUR > 24   -> X hele dage + resttimer
    """
    open_dt = day_local.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)

    ch = CLOSE_HOUR
    if ch == 24:
        # 24:00 = næste dags 00:00
        close_dt = (day_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif 0 <= ch <= 23:
        # Samme dag – med “wrap” hvis lukketid <= åbningstid
        close_dt = day_local.replace(hour=ch, minute=0, second=0, microsecond=0)
        if ch <= OPEN_HOUR:
            close_dt += timedelta(days=1)
    elif ch > 24:
        # Fx 28 => næste dag 04:00, 48 => +2 dage 00:00, osv.
        days, hour = divmod(ch, 24)
        close_dt = (day_local + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("CLOSE_HOUR must be >= 0")

    return open_dt, close_dt


def parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format; expected YYYY-MM-DD")


app = FastAPI(title="Pool & Shuffle Booking")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


class ResourceOut(BaseModel):
    id: int
    name: str
    kind: str


class AvailabilityItem(BaseModel):
    label: str            # "HH:MM"
    iso_start_local: str  # slot start (time-bucket 1 time)
    status: str           # "free" / "booked"
    booking_id: Optional[int] = None
    name: Optional[str] = None


class AvailabilityOut(BaseModel):
    date: str
    open_local: str
    close_local: str
    resources: Dict[int, List[AvailabilityItem]]


class CreateBookingIn(BaseModel):
    resource_id: int
    date: str                  # YYYY-MM-DD (forretningsdag)
    name: str
    phone: Optional[str] = None
    # VÆLG EN AF DISSE TO:
    hour: Optional[int] = Field(default=None, ge=0, le=23)  # hel time (hurtig booking)
    start_time: Optional[str] = None  # "HH:MM" (minut-booking)


class BookingOut(BaseModel):
    id: int
    resource_id: int
    name: str
    phone: Optional[str]
    start_iso_local: str
    end_iso_local: str


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/resources", response_model=List[ResourceOut])
def get_resources():
    db = SessionLocal()
    try:
        rows = db.query(Resource).order_by(Resource.kind, Resource.name).all()
        return [ResourceOut(id=r.id, name=r.name, kind=r.kind) for r in rows]
    finally:
        db.close()


@app.get("/api/availability", response_model=AvailabilityOut)
def get_availability(date: str):
    day_local = parse_date(date)
    open_dt, close_dt = business_window(day_local)

    # Byg 1-times "buckets" for visning
    slots: List[datetime] = []
    cur = open_dt
    while cur < close_dt:
        slots.append(cur)
        cur += timedelta(hours=1)

    db = SessionLocal()
    try:
        resources = db.query(Resource).order_by(Resource.kind, Resource.name).all()

        # Hent alle bookinger i forretningsvinduet
        open_utc = open_dt.astimezone(UTC)
        close_utc = close_dt.astimezone(UTC)
        bookings = db.query(Booking).filter(
            Booking.start_utc < close_utc,
            Booking.end_utc > open_utc
        ).all()

        # Map pr. resource
        by_res: Dict[int, List[Booking]] = {}
        for b in bookings:
            by_res.setdefault(b.resource_id, []).append(b)

        out: Dict[int, List[AvailabilityItem]] = {}
        for r in resources:
            row: List[AvailabilityItem] = []
            r_bookings = by_res.get(r.id, [])

            for s in slots:
                s_end = s + timedelta(hours=1)
                # Marker "booked", hvis nogen booking overlapper denne time-bucket
                overlapping = next((b for b in r_bookings
                                    if b.start_utc < s_end.astimezone(UTC) and b.end_utc > s.astimezone(UTC)), None)
                row.append(AvailabilityItem(
                    label=s.strftime("%H:%M"),
                    iso_start_local=s.isoformat(),
                    status="booked" if overlapping else "free",
                    booking_id=overlapping.id if overlapping else None,
                    name=overlapping.name if overlapping else None,
                ))
            out[r.id] = row

        return AvailabilityOut(
            date=date,
            open_local=open_dt.isoformat(),
            close_local=close_dt.isoformat(),
            resources=out
        )
    finally:
        db.close()


@app.post("/api/bookings", response_model=BookingOut, status_code=201)
def create_booking(payload: CreateBookingIn):
    day_local = parse_date(payload.date)
    open_dt, close_dt = business_window(day_local)

    # Find start_local fra enten hour eller start_time (HH:MM)
    if payload.start_time:
        try:
            hh, mm = map(int, payload.start_time.split(":"))
        except Exception:
            raise HTTPException(status_code=400, detail="start_time must be HH:MM")
        # Hvis vi krydser midnat og valgt time < OPEN_HOUR, tolkes som NÆSTE dag
        start_day = day_local + timedelta(days=1) if (CLOSE_HOUR <= OPEN_HOUR and hh < OPEN_HOUR) else day_local
        start_local = start_day.replace(hour=hh, minute=mm, second=0, microsecond=0)
    elif payload.hour is not None:
        start_local = day_local.replace(hour=payload.hour, minute=0, second=0, microsecond=0)
    else:
        raise HTTPException(status_code=400, detail="Provide either 'hour' or 'start_time' (HH:MM)")

    end_local = start_local + timedelta(hours=1)

    # Tjek åbningstid (slut skal være <= close_dt; start >= open_dt)
    if start_local < open_dt or end_local > close_dt:
        raise HTTPException(status_code=400, detail="Booking is outside opening hours")

    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)

    db = SessionLocal()
    try:
        res = db.query(Resource).filter(Resource.id == payload.resource_id).first()
        if not res:
            raise HTTPException(status_code=404, detail="Resource not found")

        # Overlap-tjek: (new.start < existing.end) AND (new.end > existing.start)
        overlap = db.query(Booking).filter(
            Booking.resource_id == payload.resource_id,
            Booking.start_utc < end_utc,
            Booking.end_utc > start_utc
        ).first()
        if overlap:
            raise HTTPException(status_code=409, detail="Slot overlaps an existing booking")

        booking = Booking(
            resource_id=payload.resource_id,
            start_utc=start_utc,
            end_utc=end_utc,
            name=payload.name.strip(),
            phone=(payload.phone or "").strip() or None
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)

        return BookingOut(
            id=booking.id,
            resource_id=booking.resource_id,
            name=booking.name,
            phone=booking.phone,
            start_iso_local=start_local.isoformat(),
            end_iso_local=end_local.isoformat(),
        )
    finally:
        db.close()


@app.get("/api/bookings", response_model=List[BookingOut])
def list_bookings(date: str):
    day_local = parse_date(date)
    open_dt, close_dt = business_window(day_local)

    db = SessionLocal()
    try:
        rows = db.query(Booking).filter(
            Booking.start_utc < close_dt.astimezone(UTC),
            Booking.end_utc > open_dt.astimezone(UTC)
        ).order_by(Booking.start_utc).all()

        out: List[BookingOut] = []
        for b in rows:
            out.append(BookingOut(
                id=b.id,
                resource_id=b.resource_id,
                name=b.name,
                phone=b.phone,
                start_iso_local=b.start_utc.astimezone(LOCAL_TZ).isoformat(),
                end_iso_local=b.end_utc.astimezone(LOCAL_TZ).isoformat(),
            ))
        return out
    finally:
        db.close()


@app.delete("/api/bookings/{booking_id}")
def delete_booking(booking_id: int):
    db = SessionLocal()
    try:
        row = db.query(Booking).filter(Booking.id == booking_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Booking not found")
        db.delete(row)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


class UpdateBookingIn(BaseModel):
    end_iso_local: Optional[str] = None  # fx "2025-08-30T23:00:00+02:00"
    add_minutes: Optional[int] = Field(default=None, ge=1, le=12*60)  # alternativ: antal min at lægge til


@app.put("/api/bookings/{booking_id}", response_model=BookingOut)
def update_booking(booking_id: int, payload: UpdateBookingIn):
    db = SessionLocal()
    try:
        row = db.query(Booking).filter(Booking.id == booking_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Booking not found")

        # Find ny slut-tid
        new_end_utc = None
        if payload.add_minutes is not None:
            new_end_utc = row.end_utc + timedelta(minutes=payload.add_minutes)
        elif payload.end_iso_local:
            try:
                dt = datetime.fromisoformat(payload.end_iso_local)
            except Exception:
                raise HTTPException(status_code=400, detail="end_iso_local must be ISO-8601")
            if dt.tzinfo is None:
                # Tolkes som lokal tid, hvis ingen TZ
                dt = dt.replace(tzinfo=LOCAL_TZ)
            new_end_utc = dt.astimezone(UTC)
        else:
            raise HTTPException(status_code=400, detail="Provide add_minutes or end_iso_local")

        if new_end_utc <= row.start_utc:
            raise HTTPException(status_code=400, detail="New end must be after start")

        # Overlap-tjek: samme resource, ikke denne booking
        conflict = db.query(Booking).filter(
            Booking.resource_id == row.resource_id,
            Booking.id != row.id,
            Booking.start_utc < new_end_utc,
            Booking.end_utc > row.start_utc
        ).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Extension overlaps another booking")

        row.end_utc = new_end_utc
        db.commit()
        db.refresh(row)

        return BookingOut(
            id=row.id,
            resource_id=row.resource_id,
            name=row.name,
            phone=row.phone,
            start_iso_local=row.start_utc.astimezone(LOCAL_TZ).isoformat(),
            end_iso_local=row.end_utc.astimezone(LOCAL_TZ).isoformat(),
        )
    finally:
        db.close()


# ---------- Routes til forsider ----------
# Public forside på "/"
@app.get("/", include_in_schema=False)
def public_home():
    return FileResponse("static/public-booking.html")

# Personale/back-end på "/staff"
@app.get("/staff", include_in_schema=False)
def staff_home():
    return FileResponse("static/index.html")

# Alias bevares
@app.get("/public", include_in_schema=False)
def public_alias():
    return FileResponse("static/public-booking.html")

