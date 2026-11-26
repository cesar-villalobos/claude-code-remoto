# Política de seguridad

Mando permite controlar Claude Code, y por lo tanto **ejecutar comandos en el equipo donde corre**, desde un navegador. Un fallo de seguridad puede tener consecuencias serias, por eso agradecemos los reportes responsables.

## Cómo reportar una vulnerabilidad
**No abras un issue público.** Usa el reporte privado de GitHub: pestaña **Security** → **Report a vulnerability**.

Incluye, si puedes:
- versión o commit afectado;
- pasos para reproducirlo y su impacto (por ejemplo, saltarse el login o la protección CSRF, o ejecutar acciones sin aprobación);
- una propuesta de corrección, si la tienes.

Intentaremos responder en un plazo de 7 días y coordinar la publicación del arreglo antes de divulgar los detalles.

## Versiones con soporte
Solo la última versión publicada (rama `main`) recibe correcciones de seguridad.

## Modelo de seguridad
Esto es lo que el proyecto **sí** intenta proteger:
- **Acceso solo con token:** token aleatorio de 32 caracteres, cookie `HttpOnly` y `SameSite=Strict`, y límites de intentos por IP y globales.
- **Segundo factor opcional (TOTP, RFC 6238)**, sin reutilización de códigos, también para el enlace/QR con token.
- **Sesiones por dispositivo** revocables al instante; el servidor guarda solo su hash.
- **Auditoría local** de inicios de sesión, decisiones, reglas y acciones de Claude.
- **Solo red local:** se rechazan siempre las IPs públicas. El acceso desde fuera de casa es por VPN.
- **Protección contra CSRF y clickjacking:** cabecera propia obligatoria en la API, Content-Security-Policy y `X-Frame-Options: DENY`.
- **Aprobación humana:** cada herramienta no pre-aprobada requiere decisión explícita.
- **Web Push opcional y sin datos:** si se activa en un dispositivo, el aviso va cifrado (RFC 8291) y solo dice «decisión» o «terminó»; el servidor solo envía a servicios de push conocidos (sin peticiones a la red interna).

Y esto queda **fuera de su alcance** (responsabilidad de quien lo instala):
- La seguridad del equipo anfitrión, de la cuenta de Claude Code y de las credenciales de los servidores MCP.
- Lo que se apruebe en las tarjetas de decisión: el proyecto no evalúa si un comando es dañino.
- Exponer el servicio a internet por otros medios (redirigir puertos, proxys inversos, túneles públicos). El acceso remoto previsto es la VPN; ver [docs/vpn-tailscale.md](docs/vpn-tailscale.md).
- El uso en equipos corporativos sin autorización de su área de TI o de seguridad.

## Buenas prácticas para quien lo usa
- No compartas el token, el QR ni capturas de la terminal. Si sospechas que se filtró, rótalo desde 🔐 Seguridad y revisa la auditoría.
- Activa el 2FA si el servicio es accesible fuera de tu red.
- Mantén `AUTO_APPROVE_TOOLS` y `AUTO_APPROVE_PREFIXES` solo con herramientas de lectura.
- Usa HTTPS si la red no es tuya, y prefiere la VPN frente a abrir puertos.
- Mantén actualizados Claude Code, Python y las dependencias.
