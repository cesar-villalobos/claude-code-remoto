// Service worker de Claude Remoto.
// Necesario para mostrar notificaciones del sistema en Android (Chrome no permite
// `new Notification()` en móviles) y para responder desde la propia notificación.
// No guarda nada en caché: la app siempre requiere el servidor local.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

async function focusApp(ref) {
  const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const w of wins) {
    if ('focus' in w) {
      await w.focus();
      if (ref) w.postMessage({ type: 'focus-card', ref });
      return;
    }
  }
  await self.clients.openWindow('/' + (ref ? '#d-' + ref : ''));
}

// Web Push: avisos con la app cerrada. Son genéricos a propósito: pasan por el servicio de push
// del navegador (en internet), así que solo dicen qué pasó; el detalle se ve al abrir la app.
const PUSH_TEXT = {
  decision: ['Claude necesita tu decisión', 'Abre Claude Remoto para revisarla.'],
  done: ['Claude terminó una tarea', 'Abre Claude Remoto para ver el resultado.'],
  error: ['Una tarea terminó con error', 'Abre Claude Remoto para ver qué pasó.'],
  test: ['Claude Remoto · prueba', 'Así llegarán los avisos con la app cerrada.'],
  summary: ['Resumen del día', 'Abre Claude Remoto para ver lo que hizo Claude hoy.'],
};
self.addEventListener('push', (e) => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (_) { /* aviso sin datos: genérico */ }
  const [title, body] = PUSH_TEXT[m.k] || PUSH_TEXT.decision;
  const tag = m.k === 'decision' && m.ref ? 'cr-' + m.ref : m.k === 'test' ? 'cr-test' : m.k === 'summary' ? 'cr-summary' : 'cr-done';
  e.waitUntil(self.registration.showNotification(title, {
    body, tag, renotify: true, icon: '/icon.svg', badge: '/icon.svg',
    requireInteraction: m.k === 'decision', vibrate: [200, 100, 200],
    data: { ref: m.k === 'decision' ? m.ref : '' },   // sin botones Aprobar/Rechazar: no se ve qué se aprueba
  }));
});
// El navegador renovó la suscripción: se registra la nueva (la cookie de la sesión va con la petición)
self.addEventListener('pushsubscriptionchange', (e) => {
  e.waitUntil((async () => {
    const opts = e.oldSubscription && e.oldSubscription.options;
    const sub = e.newSubscription || (opts && await self.registration.pushManager.subscribe(opts));
    if (!sub) return;
    await fetch('/api/cmd', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CR': '1' },
      body: JSON.stringify({ cmd: 'push_subscribe', sub: sub.toJSON() }) });
  })());
});

self.addEventListener('notificationclick', (e) => {
  const n = e.notification;
  const { ref } = n.data || {};
  n.close();
  e.waitUntil((async () => {
    // Botones «Aprobar» / «Rechazar» de la notificación: decide sin abrir la app
    if (ref && (e.action === 'y' || e.action === 'n')) {
      try {
        const r = await fetch('/api/decide', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CR': '1' },
          body: JSON.stringify({ ref, choice: e.action }),
        });
        if (r.ok) return;
      } catch (_) { /* sin conexión: abre la app para decidir ahí */ }
    }
    await focusApp(ref);
  })());
});
