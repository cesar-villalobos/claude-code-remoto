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
