/* end-alert.js — Booking alarm (drop-in, no server changes)
   - Viser alarm ALERT_MINUTES før sluttid
   - Finder tider i DOM: "HH:MM–HH:MM" eller "HH:MM-HH:MM"
   - Prøver at finde bordnr fra tekst "Bord 3" / "Table 3"
   - Snooze 2 min, ekstra bip ved tid=0
   - Konfig: window.ALERT_MINUTES = 5 (kan sættes i din template)
*/
(() => {
  // ========= Config =========
  const ALERT_MINUTES = Number(window.ALERT_MINUTES ?? 5);
  const ALERT_MS = ALERT_MINUTES * 60 * 1000;

  // ========= CSS (injiceres automatisk) =========
  const CSS = `
#endAlertBar{position:fixed;left:0;right:0;top:0;background:rgba(255,193,7,.95);color:#000;padding:10px 14px;z-index:9999;display:none;align-items:center;gap:10px;box-shadow:0 4px 14px rgba(0,0,0,.2);font-weight:600;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif}
#endAlertBar.show{display:flex}
#endAlertBar button{margin-left:auto;font-weight:700;border:0;border-radius:10px;padding:8px 12px;cursor:pointer;background:#111;color:#fff}
.flash-warning{animation:flashWarn 1s linear infinite}
@keyframes flashWarn{0%,100%{box-shadow:0 0 0 0 rgba(255,193,7,0)}50%{box-shadow:0 0 0 8px rgba(255,193,7,.4)}}
.almost-done{background:rgba(255,193,7,.18)!important}
  `.trim();

  function injectCSS() {
    if (document.getElementById('endAlertCSS')) return;
    const style = document.createElement('style');
    style.id = 'endAlertCSS';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // ========= Banner UI =========
  function ensureBanner() {
    let bar = document.getElementById('endAlertBar');
    if (bar) return bar;

    bar = document.createElement('div');
    bar.id = 'endAlertBar';
    bar.innerHTML = `
      <span id="endAlertMsg">En booking slutter snart</span>
      <div style="display:flex;gap:8px;margin-left:auto">
        <button id="snoozeEndAlert" type="button">Snooze 2 min</button>
        <button id="closeEndAlert" type="button">Luk</button>
      </div>
    `;
    document.body.appendChild(bar);
    return bar;
  }

  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
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

  function humanMins(ms) { return Math.max(0, Math.round(ms / 60000)); }

  let snoozedUntil = 0;
  function showAlert(tableNo, msLeft) {
    if (Date.now() < snoozedUntil) return;
    const bar = ensureBanner();
    const msg = bar.querySelector('#endAlertMsg');
    msg.textContent = tableNo
      ? `Bord ${tableNo} slutter om ${humanMins(msLeft)} min`
      : `Booking slutter om ${humanMins(msLeft)} min`;
    bar.classList.add('show');
    beep();
  }
  function hideAlert() {
    const bar = document.getElementById('endAlertBar');
    if (bar) bar.classList.remove('show');
  }

  function wireButtons() {
    const bar = ensureBanner();
    bar.querySelector('#snoozeEndAlert')?.addEventListener('click', () => {
      snoozedUntil = Date.now() + (2 * 60 * 1000);
      hideAlert();
    });
    bar.querySelector('#closeEndAlert')?.addEventListener('click', hideAlert);
  }

  // ========= Parsing af DOM =========
  // Prioritet: data-end (ISO), data-end-local (HH:mm), ellers parse fra tekst "HH:MM–HH:MM"
  const TIME_RANGE = /\b(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\b/;
  const TABLE_NUM = /\b(?:Bord|Table)\s*(\d+)\b/i;

  function parseEndFromNode(node) {
    const el = /** @type {HTMLElement} */ (node);
    const iso = el.dataset?.end;
    if (iso) {
      const d = new Date(iso);
      if (!isNaN(d)) return d;
    }
    const hhmm = el.dataset?.endLocal;
    if (hhmm && /^\d{1,2}:\d{2}$/.test(hhmm)) {
      const [hh, mm] = hhmm.split(':').map(Number);
      const now = new Date();
      const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hh, mm, 0, 0);
      if (end < now) end.setDate(end.getDate() + 1);
      return end;
    }

    // Fallback: parse fra tekst
    const txt = el.innerText || '';
    const m = txt.match(TIME_RANGE);
    if (m) {
      const [ , sH, sM, eH, eM ] = m.map(Number);
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), sH, sM, 0, 0);
      let end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), eH, eM, 0, 0);
      // Over midnat (fx 23:30–00:30)
      if (end <= start) end.setDate(end.getDate() + 1);
      // Hvis end allerede passeret men vi er tæt på midnat, rul én dag frem
      if (end < now && (now - end) < (6 * 60 * 60 * 1000)) end.setDate(end.getDate() + 1);
      return end;
    }
    return null;
  }

  function parseTableNo(node) {
    const el = /** @type {HTMLElement} */ (node);
    if (el.dataset?.table && /^\d+$/.test(el.dataset.table)) return el.dataset.table;
    const m = (el.innerText || '').match(TABLE_NUM);
    return m ? m[1] : '';
  }

  // Find kandidater:
  function findBookingNodes() {
    const nodes = new Set();
    // Mest sandsynlige markører
    document.querySelectorAll('[data-end],[data-end-local],.booking,[data-table]').forEach(n => nodes.add(n));
    // Fallback: find elementer der indeholder tids-interval
    // (for at skåne performance, kig kun på tekst-bærende elementer)
    document.querySelectorAll('div,li,td,section,article,p,span').forEach(el => {
      if (TIME_RANGE.test(el.innerText || '')) nodes.add(el);
    });
    return Array.from(nodes);
  }

  // ========= Planlægning =========
  const scheduled = new WeakSet();

  function paintAlmostDone(el, msLeft) {
    if (msLeft <= (ALERT_MS) && msLeft > 0) {
      el.classList.add('almost-done');
    } else {
      el.classList.remove('almost-done');
    }
  }

  function scheduleFor(el) {
    if (scheduled.has(el)) return;
    const end = parseEndFromNode(el);
    if (!end) return;

    scheduled.add(el);
    const tableNo = parseTableNo(el);

    const tick = () => {
      const left = end.getTime() - Date.now();
      paintAlmostDone(el, left);
      if (left <= 0) return; // done; ekstra bip sættes separat
      if (left <= ALERT_MS) {
        el.classList.add('flash-warning');
        showAlert(tableNo, left);
        // planlæg bip ved 0
        setTimeout(() => beep(), Math.max(0, end.getTime() - Date.now()));
      } else {
        // planlæg alarmen præcist
        const msTillAlert = left - ALERT_MS;
        setTimeout(() => {
          el.classList.add('flash-warning');
          showAlert(tableNo, ALERT_MS);
          setTimeout(() => beep(), Math.max(0, end.getTime() - Date.now()));
        }, msTillAlert);
      }
    };

    // Kald nu (håndter cases hvor vi allerede er < ALERT_MS)
    tick();
  }

  function init() {
    injectCSS();
    ensureBanner();
    wireButtons();
    // Planlæg for nuværende
    findBookingNodes().forEach(scheduleFor);
    // Re-scan hvert minut for nye elementer
    setInterval(() => findBookingNodes().forEach(scheduleFor), 60 * 1000);
    // Opdater farver hvert 15s så "almost-done" er live
    setInterval(() => {
      findBookingNodes().forEach(el => {
        const end = parseEndFromNode(el);
        if (!end) return;
        paintAlmostDone(el, end.getTime() - Date.now());
      });
    }, 15 * 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
