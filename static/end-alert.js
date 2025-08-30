/* end-alert.js v2 — blink + actions (“Færdig” / “+1 time”)
   - Finder kort via deres Slet-knap (button[data-del])
   - Parser tider på formen "HH:MM–HH:MM" ELLER "HH.MM–HH.MM" (dansk)
   - Når der er <= ALERT_MINUTES tilbage, begynder kort at blinke
   - Viser to knapper:
       Færdig   -> stopper blink for det kort (ingen server-ændring)
       +1 time  -> PUT /api/bookings/:id { add_minutes: 60 } og reloader listen
*/
(() => {
  const ALERT_MINUTES = Number(window.ALERT_MINUTES ?? 5);
  const ALERT_MS = ALERT_MINUTES * 60 * 1000;

  // CSS
  const CSS = `
@keyframes blinkAmber{0%,100%{box-shadow:0 0 0 0 rgba(255,193,7,0)}50%{box-shadow:0 0 0 12px rgba(255,193,7,.35)}}
.booking-blink{animation:blinkAmber 1s linear infinite}
.ending-actions{display:flex;gap:.5rem;margin-top:.5rem}
.ending-actions button{font-size:.75rem;padding:.35rem .6rem;border-radius:.5rem}
.btn-done{background:#111;color:#fff}
.btn-extend{background:#059669;color:#fff}
  `.trim();
  function injectCSS(){
    if (document.getElementById('endAlertCSS')) return;
    const style = document.createElement('style');
    style.id = 'endAlertCSS';
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  injectCSS();

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

  // Parse "HH:MM–HH:MM" ELLER "HH.MM–HH.MM"
  const RANGE = /\b(\d{1,2})[:.](\d{2})\s*[-–]\s*(\d{1,2})[:.](\d{2})\b/;

  function parseEndFromCard(card){
    const txt = (card.innerText || '').replace(/\s+/g,' ').trim();
    const m = txt.match(RANGE);
    if (!m) return null;
    const sH = Number(m[1]), sM = Number(m[2]);
    const eH = Number(m[3]), eM = Number(m[4]);
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), sH, sM, 0, 0);
    let end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), eH, eM, 0, 0);
    if (end <= start) end.setDate(end.getDate()+1); // over midnat
    if (end < now && (now - end) < 6*60*60*1000) end.setDate(end.getDate()+1);
    return end;
  }

  function findIdFromCard(card){
    const btn = card.querySelector('button[data-del]');
    if (!btn) return null;
    const id = Number(btn.getAttribute('data-del'));
    return Number.isFinite(id) ? id : null;
  }

  function addActions(card){
    if (card.querySelector('.ending-actions')) return;
    const id = findIdFromCard(card);
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
      const btn = e.currentTarget;
      btn.disabled = true;
      try{
        if (!id) throw new Error('Mangler booking-id');
        const res = await fetch(`/api/bookings/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ add_minutes: 60 })
        });
        if (!res.ok){
          const t = await res.text();
          throw new Error(t || 'Kunne ikke udvide tiden');
        }
        // hvis siden har fetchAll() globalt, brug den – ellers refresh
        if (typeof window.fetchAll === 'function') {
          await window.fetchAll();
        } else {
          location.reload();
        }
      }catch(err){
        alert(err?.message || err);
        btn.disabled = false;
      }
    });
  }

  const scheduled = new WeakSet();

  function scheduleCard(card){
    if (scheduled.has(card)) return;
    const end = parseEndFromCard(card);
    if (!end) return;
    scheduled.add(card);

    const tick = ()=>{
      if (card.dataset.ack === '1') return;
      const left = end.getTime() - Date.now();
      if (left <= 0) return;
      if (left <= ALERT_MS){
        card.classList.add('booking-blink');
        addActions(card);
        beep();
      } else {
        setTimeout(()=>{
          if (card.isConnected) {
            card.classList.add('booking-blink');
            addActions(card);
            beep();
          }
        }, left - ALERT_MS);
      }
    };
    tick();
  }

  function scan(){
    // Et "kort" defineres som et element der indeholder Slet-knappen
    document.querySelectorAll('button[data-del]').forEach(btn=>{
      const card = btn.closest('div'); // i dit layout er knappen direkte child
      if (card) scheduleCard(card);
    });
  }

  // Initialt og løbende
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', scan);
  } else {
    scan();
  }
  setInterval(scan, 30*1000);
})();
