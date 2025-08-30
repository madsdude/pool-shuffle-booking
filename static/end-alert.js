/* end-alert.js v3 — blink + Færdig / +1 time */
(() => {
  const ALERT_MINUTES = Number(window.ALERT_MINUTES ?? 5);
  const ALERT_MS = ALERT_MINUTES * 60 * 1000;

  // ---------- CSS ----------
  const CSS = `
@keyframes blinkAmber { 0%,100%{box-shadow:0 0 0 0 rgba(255,193,7,0)} 50%{box-shadow:0 0 0 12px rgba(255,193,7,.35)} }
.booking-blink{ animation:blinkAmber 1s linear infinite; }
.ending-actions{ display:flex; gap:.5rem; margin-top:.5rem; }
.ending-actions button{ font-size:.75rem; padding:.35rem .6rem; border-radius:.5rem; }
.btn-done{ background:#111; color:#fff; }
.btn-extend{ background:#059669; color:#fff; }
  `.trim();
  function injectCSS(){
    if (document.getElementById('endAlertCSS')) return;
    const s = document.createElement('style');
    s.id = 'endAlertCSS';
    s.textContent = CSS;
    document.head.appendChild(s);
  }
  injectCSS();

  // ---------- Utils ----------
  function beep(){
    try{
      const ctx = new (window.AudioContext||window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.type='sine'; o.frequency.value=880;
      g.gain.setValueAtTime(0.0001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime+0.02);
      o.start();
      setTimeout(()=>{ g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime+0.02); o.stop(); ctx.close(); }, 400);
    }catch{}
    if (navigator.vibrate) navigator.vibrate([150,100,150]);
  }

  // Tids-interval: HH:MM–HH:MM eller HH.MM–HH.MM (både – og -)
  const RANGE = /\b(\d{1,2})[:.](\d{2})\s*[-–]\s*(\d{1,2})[:.](\d{2})\b/;

  // Find "kortet" ved at gå op fra Slet-knappen, indtil vi finder et element der indeholder tids-intervallet
  function findCardFromButton(btn){
    let el = btn.parentElement;
    while (el && el !== document.body) {
      const txt = (el.innerText || '').trim();
      if (RANGE.test(txt)) return el;
      el = el.parentElement;
    }
    return btn.parentElement || btn;
  }

  function parseEndFromElement(el){
    const txt = (el.innerText || '');
    const m = txt.match(RANGE);
    if (!m) return null;
    const sH = parseInt(m[1],10), sM = parseInt(m[2],10);
    const eH = parseInt(m[3],10), eM = parseInt(m[4],10);
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), sH, sM, 0, 0);
    let end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), eH, eM, 0, 0);
    if (end <= start) end.setDate(end.getDate()+1);                 // over midnat
    if (end < now && (now - end) < 6*60*60*1000) end.setDate(end.getDate()+1);
    return end;
  }

  function findIdFromCard(card){
    const btn = card.querySelector('button[data-del]');
    if (!btn) return null;
    const n = Number(btn.getAttribute('data-del'));
    return Number.isFinite(n) ? n : null;
  }

  async function extendBooking(id, minutes=60){
    try{
      const res = await fetch(`/api/bookings/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ add_minutes: minutes })
      });
      if (res.ok) return true;
    }catch{}
    // fallback-endpoint hvis du hellere vil have en separat extend-route
    try{
      const res2 = await fetch(`/api/bookings/extend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, add_minutes: minutes })
      });
      if (res2.ok) return true;
    }catch{}
    return false;
  }

  function addActions(card){
    if (card.querySelector('.ending-actions')) return;
    const bar = document.createElement('div');
    bar.className = 'ending-actions';
    bar.innerHTML = `
      <button type="button" class="btn-done">Færdig</button>
      <button type="button" class="btn-extend">+1 time</button>
    `;
    card.appendChild(bar);

    bar.querySelector('.btn-done').addEventListener('click', ()=>{
      card.classList.remove('booking-blink');
      bar.remove();
      card.dataset.ack = '1';
    });

    bar.querySelector('.btn-extend').addEventListener('click', async (e)=>{
      const id = findIdFromCard(card);
      const btn = e.currentTarget;
      btn.disabled = true;
      try{
        if (!id) throw new Error('Mangler booking-id');
        const ok = await extendBooking(id, 60);
        if (ok){
          if (typeof window.fetchAll === 'function') await window.fetchAll();
          else location.reload();
        } else {
          alert('Kunne ikke udvide tiden (+1 time). Har du PUT /api/bookings/<id>?');
          btn.disabled = false;
        }
      }catch(err){
        alert(err?.message || err);
        btn.disabled = false;
      }
    });
  }

  const scheduled = new WeakMap(); // card -> timeoutId (for sanity)
  function trigger(card){
    if (card.dataset.ack === '1') return;
    card.classList.add('booking-blink');
    addActions(card);
    beep();
  }

  function scheduleCard(card, end){
    if (scheduled.has(card)) return;
    const left = end.getTime() - Date.now();
    if (left <= 0) return;
    if (left <= ALERT_MS) {
      trigger(card);
    } else {
      const id = setTimeout(()=> trigger(card), left - ALERT_MS);
      scheduled.set(card, id);
    }
  }

  function scan(){
    document.querySelectorAll('button[data-del]').forEach(btn=>{
      const card = findCardFromButton(btn);
      if (!card) return;
      if (card.dataset.ack === '1') return;
      if (scheduled.has(card)) return;
      const end = parseEndFromElement(card);
      if (!end) return;
      scheduleCard(card, end);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scan);
  } else {
    scan();
  }
  setInterval(scan, 15000);
})();
