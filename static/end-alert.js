// ====== KONFIG ======
const ALERT_MINUTES = 10; // vis knapper og blink når der er <= 5 min tilbage

// ====== HJÆLPEFUNKTIONER ======
function injectBlinkCSS() {
  if (document.getElementById('blinkCSS')) return;
  const s = document.createElement('style');
  s.id = 'blinkCSS';
  s.textContent = `
@keyframes blinkAmber { 0%,100%{box-shadow:0 0 0 0 rgba(255,193,7,0)} 50%{box-shadow:0 0 0 10px rgba(255,193,7,.35)} }
.blink-amber { animation: blinkAmber 1s linear infinite; }
.ending-actions { display:flex; gap:8px; margin-top:8px; }
.ending-actions button { font-size:.75rem; padding:.35rem .6rem; border-radius:.5rem; }
.btn-done { background:#111; color:#fff; }
.btn-extend { background:#059669; color:#fff; }
  `.trim();
  document.head.appendChild(s);
}
injectBlinkCSS();

function toLocalISO(d) {
  const pad = n => String(n).padStart(2,'0');
  const off = -d.getTimezoneOffset(); // mins east of UTC
  const sign = off >= 0 ? '+' : '-';
  const hh = pad(Math.floor(Math.abs(off)/60));
  const mm = pad(Math.abs(off)%60);
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}${sign}${hh}:${mm}`;
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = 'sine'; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.02);
    o.start();
    setTimeout(() => {
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.02);
      o.stop(); ctx.close();
    }, 400);
  } catch {}
  if (navigator.vibrate) navigator.vibrate([150,100,150]);
}

// Forsøg PUT /api/bookings/:id med ny endetid. Fald tilbage til POST /api/bookings/extend (id, add_minutes)
async function updateBookingEnd(id, newEndDate) {
  const body = JSON.stringify({ end_iso_local: toLocalISO(newEndDate) });

  try {
    const res = await fetch(`/api/bookings/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body
    });
    if (res.ok) return true;
  } catch {}

  try {
    const res2 = await fetch(`/api/bookings/extend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, add_minutes: 60 })
    });
    if (res2.ok) return true;
  } catch {}

  return false;
}

function addEndingButtons(card) {
  if (card.querySelector('.ending-actions')) return;
  const bar = document.createElement('div');
  bar.className = 'ending-actions';
  bar.innerHTML = `
    <button type="button" class="btn-done">Færdig</button>
    <button type="button" class="btn-extend">+1 time</button>
  `;
  card.appendChild(bar);

  // FÆRDIG → stop blink (ingen serverændring)
  bar.querySelector('.btn-done').addEventListener('click', () => {
    card.classList.remove('blink-amber');
    bar.remove();
    card.dataset.ack = '1';
  });

  // +1 TIME → PUT/PATCH til server og reload listen
  bar.querySelector('.btn-extend').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const id = Number(card.dataset.id);
      const endNow = new Date(card.dataset.end);
      const newEnd = new Date(endNow.getTime() + 60*60*1000);
      const ok = await updateBookingEnd(id, newEnd);
      if (ok) {
        await fetchAll(); // henter nye tider og re-render
      } else {
        alert('Kunne ikke udvide tiden (+1 time). Mangler API?');
        btn.disabled = false;
      }
    } catch (err) {
      alert('Fejl: ' + (err?.message || err));
      btn.disabled = false;
    }
  });
}

function scheduleBlink(card, endDate) {
  const alertMs = ALERT_MINUTES * 60 * 1000;
  const left = endDate.getTime() - Date.now();

  const trigger = () => {
    if (card.dataset.ack === '1') return; // allerede kvitteret
    card.classList.add('blink-amber');
    addEndingButtons(card);
    beep();
  };

  if (left <= 0) return;            // allerede slut
  if (left <= alertMs) trigger();   // start med det samme
  else setTimeout(trigger, left - alertMs);
}

// ====== Dagens bookinger (RENDER + ALARM/KNAPPER) ======
bookingsBox.innerHTML = '';
if (!todays.length) {
  bookingsBox.innerHTML = '<div class="text-sm text-gray-600">Ingen bookinger endnu.</div>';
} else {
  for (const b of todays) {
    const start = new Date(b.start_iso_local);
    const end   = new Date(b.end_iso_local);

    const name = resourceName(b.resource_id);         // fx "Pool 3"
    const tableNo = (name || '').match(/\d+/)?.[0] || '';

    const card = document.createElement('div');
    card.className = 'bg-white border rounded p-3 flex items-start justify-between gap-3';
    card.dataset.id  = String(b.id);
    card.dataset.end = b.end_iso_local;

    card.innerHTML = `
      <div>
        <div class="font-semibold">${name}</div>
        <div class="text-sm text-gray-700">
          ${start.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}
          – ${end.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}
        </div>
        <div class="text-sm">${b.name}${b.phone ? ' · ' + b.phone : ''}</div>
      </div>
      <div class="flex items-start gap-2">
        <button data-del="${b.id}" class="px-2 py-1 text-xs rounded bg-red-600 text-white hover:bg-red-700">Slet</button>
      </div>
    `;

    bookingsBox.appendChild(card);

    // Planlæg blink + knapper når der er ALERT_MINUTES tilbage
    scheduleBlink(card, end);
  }

  // Slet-knapper
  bookingsBox.querySelectorAll('button[data-del]').forEach(btnDel => {
    btnDel.addEventListener('click', async (e) => {
      const id = Number(e.currentTarget.dataset.del);
      if (!confirm('Slet booking #' + id + '?')) return;
      const res = await fetch('/api/bookings/' + id, { method: 'DELETE' });
      if (res.ok) await fetchAll();
      else alert('Kunne ikke slette booking.');
    });
  });
}

// Genindlæs-knap (som før)
btn.addEventListener('click', fetchAll);
fetchAll();
