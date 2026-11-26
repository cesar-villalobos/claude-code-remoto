#!/usr/bin/env python3
"""
Mando (LAN): control de Claude Code desde el celular, solo por red local.

- Servidor web local (sin servicios externos: ni Telegram ni Slack).
- Abres http://<IP-de-tu-PC>:8765 en el celular, en la misma red Wi-Fi.
- Chat con Claude Code + tarjetas de decisión (Aprobar / Rechazar / Siempre,
  y botones por opción para las preguntas de Claude).
- Seguridad: token de acceso, solo IPs de redes privadas, sesiones por dispositivo,
  2FA opcional, auditoría y HTTPS opcional con certificado propio.
- Claude Code usa tu configuración habitual (API de Claude o gateway de IA de tu empresa)
  y tus servidores MCP.

Solo biblioteca estándar + claude-agent-sdk (python-dotenv y qrcode opcionales).
El código está en el paquete mando/ (ver mando/__init__.py).

Uso:
    python server.py                   # inicia el servidor
    python server.py --instalar-inicio # arranque automático al iniciar sesión (--quitar-inicio, --estado-inicio)
    python server.py --mostrar-acceso  # enlace con token y QR, sin iniciar
    python server.py --desactivar-2fa  # quita el segundo factor (si perdiste el teléfono)
"""

from mando.app import run

if __name__ == "__main__":
    run()
