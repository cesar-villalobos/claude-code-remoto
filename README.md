# Claude Remoto (red local)

Controla Claude Code desde el navegador del celular, **solo dentro de tu red local**. El control no pasa por Telegram, Slack ni ningún otro servicio externo.

> **Proyecto independiente y no oficial**, sin afiliación con Anthropic ni respaldo de su parte. «Claude» y «Claude Code» son marcas de Anthropic. Para la función oficial de control remoto, consulta la documentación de Claude Code.

## ¿Para quién es?
Para quienes usan **Claude Code con la API**, y especialmente **a través de un gateway de IA corporativo**: el caso en que la empresa entrega acceso a Claude mediante su propio proxy o gateway (`ANTHROPIC_BASE_URL`), con su autenticación y sus controles.

En esos casos no está disponible el [Remote Control oficial](https://code.claude.com/docs/en/remote-control) de Claude Code. Según su documentación, requiere iniciar sesión con una suscripción de claude.ai (Pro, Max, Team o Enterprise): no admite claves de API, y tampoco funciona cuando `ANTHROPIC_BASE_URL` apunta a un gateway o proxy, ni con Amazon Bedrock, Google Cloud o Microsoft Foundry.

Claude Remoto cubre ese hueco:
- **Funciona con tu configuración actual:** la clave de API o el gateway de la empresa, con su proxy y certificados, y los servidores MCP que uses.
- **Lo que haces desde el celular no pasa por ningún servicio externo:** viaja por tu red local, o por tu VPN cuando estás fuera.

Si tienes una suscripción compatible y no usas un gateway, el Remote Control oficial puede ser más simple para ti.

<details>
<summary><b>🇬🇧 In English</b></summary>

**Claude Remoto** lets you drive [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) from your phone's browser, **over your local network only**. It is meant for people who use Claude Code **through the API, especially via a corporate AI gateway** (`ANTHROPIC_BASE_URL`), where the official [Remote Control](https://code.claude.com/docs/en/remote-control) is not available (it requires a claude.ai subscription and does not support API keys, gateways or Bedrock/Vertex/Foundry). Nothing goes through Telegram, Slack or any other third-party service; for access away from home, use a VPN (a Tailscale guide is included). The user interface and docs are in **Spanish** for now.

- **Approve or reject** each tool call from cards: diffs for file edits, plan review, answers to Claude's questions, rejection with a comment.
- **Real-time streaming**, synced across devices without refreshing. Sound alerts and system notifications with Approve/Reject buttons.
- **Pattern rules** (always block / always allow), permission modes, per-device sessions, optional **TOTP 2FA** and a local **audit log**.
- **Parallel sessions** in tabs, each with its own Claude Code process, folder and history; optional **autostart** at login (Windows, macOS, Linux).
- **Voice input** transcribed on your own PC (Whisper), optional spoken replies, prompt templates, `/` command autocomplete, Jira links and screenshot attachments.
- Python standard library plus `claude-agent-sdk`; works with the Claude API or a corporate AI gateway and any MCP servers you have configured.

Quick start: `pip install -r requirements.txt`, then `python server.py` and scan the QR code. Read the security notice below and [SECURITY.md](SECURITY.md) before using it on a shared or corporate network.

This is an independent, unofficial project, not affiliated with or endorsed by Anthropic. Contributions and issues in English are welcome.
</details>

> ⚠️ **Aviso de seguridad.** Quien tenga el token de acceso puede ejecutar comandos en tu equipo a través de Claude Code.
> - Úsalo solo en redes en las que confíes.
> - No compartas el token ni el QR.
> - Para acceder desde fuera de casa, usa la [VPN](docs/vpn-tailscale.md): el servidor no acepta conexiones desde internet.
> - En equipos corporativos, consúltalo antes con tu área de TI.
>
> Más detalles en [SECURITY.md](SECURITY.md).

```
Celular (navegador) ──Wi-Fi local──▶ tu PC :8765 ──▶ Claude Code ──▶ API de Claude (o gateway de tu empresa) / servidores MCP
```

Claude Code sigue necesitando acceso a internet para hablar con la API de Claude (o con el gateway de IA de tu empresa, si usas uno) y con los servidores MCP que tengas configurados. Lo que queda **solo en la red local** es la conexión entre el celular y tu PC.

## Qué incluye
- Chat con Claude Code y Markdown, con **respuestas en tiempo real**: el texto aparece mientras Claude lo escribe, con un cursor parpadeante, y se ve igual en todos los dispositivos aunque te conectes a mitad de la respuesta. Se desactiva con `STREAM_TEXT=0`.
- Tarjetas de decisión con botones: ✅ Aprobar / ❌ Rechazar / ♾️ Siempre. Las preguntas de Claude muestran un botón por opción, con selección múltiple cuando corresponde.
- **Reglas de aprobación por patrón** (☰ → ⚖️ Reglas, o «⚖️ Crear regla para casos así» en una tarjeta):
  - **⛔ Bloquear siempre:** se aplica en cualquier modo, incluso en «Aceptar ediciones», mediante un hook que se ejecuta antes de cada herramienta. Claude recibe el motivo. Vienen reglas predeterminadas que puedes borrar: `rm -rf`, `git push --force`, `curl … | sh`, `sudo` y la edición de `.env`.
  - **✅ Aprobar siempre:** evita la tarjeta para lo que apruebas siempre, por ejemplo `git status*`, `npm test*` o `mcp__miservidor__get*` (las lecturas de un servidor MCP). Por seguridad, nunca se aplica a comandos encadenados (`&&`, `;`, `|`, `$(…)`, redirecciones): `npm test && rm -rf x` vuelve a preguntar.
  - Tipos: comando, edición de archivos, lectura, herramienta MCP y web. Los patrones usan `*` como comodín y las rutas se comparan absolutas o relativas a la carpeta. Cada aprobación o bloqueo por regla queda anotado en el chat.
- **Modo de permisos desde la interfaz:** el botón junto a la carpeta (🛡️ *Preguntar*) abre el selector:
  - 🛡️ **Preguntar:** pide aprobación para todo lo que no está pre-aprobado (por defecto).
  - ✏️ **Aceptar ediciones:** aprueba solo las ediciones de archivos; comandos, Jira y lo demás siguen preguntando.
  - 📋 **Solo planificar:** Claude investiga y propone un plan sin tocar nada. El plan llega en una tarjeta con formato y tres opciones: **Aprobar plan**, **Aprobar y aceptar ediciones** o **pedir cambios** con un comentario.

  El cambio se aplica en caliente, incluso con una tarea en curso, se ve en todos los dispositivos y se guarda entre reinicios. El modo inicial se define con `PERMISSION_MODE`. Por seguridad no se ofrece el modo que se salta todos los permisos.
- **Cola de mensajes:** mientras Claude trabaja puedes seguir enviando. Cada mensaje aparece como «⏳ en cola» en todos tus dispositivos y se ejecuta solo cuando termina la tarea actual, en orden, como una tarea normal con sus decisiones.
  - ✕ quita un mensaje de la cola. ⏹ detiene la tarea y vacía la cola.
  - Cada pestaña tiene su propia cola, de hasta 10 mensajes. Las plantillas directas también se pueden encolar.
  - Si el servidor se reinicia, los mensajes en cola quedan marcados como no enviados.
- **Elegir el modelo** (🧠 junto al modo): cada pestaña puede usar un modelo distinto, por ejemplo uno más rápido para tareas simples.
  - El cambio se aplica en caliente, también con una tarea en curso, y se guarda entre reinicios.
  - «Predeterminado» es el que tengas configurado en Claude Code.
  - La lista se define con `CLAUDE_MODELS`. Con un gateway, pon los nombres exactos que acepte, por ejemplo `default,claude-sonnet-5,claude-opus-5`.
- **Costo y uso** (☰ → 💲 Uso): totales de hoy, 7 y 30 días, un gráfico por día y el desglose por pestaña y por modelo. Cada tarea muestra al terminar su costo y sus tokens.
  - Se guarda en `~/.claude-remoto-usage.jsonl`, solo legible por tu usuario y **sin el texto de las tareas**.
  - El costo es la estimación de Claude Code según los precios públicos. Con un gateway corporativo el cobro real puede ser distinto, por eso también se muestran los tokens.
- **Aviso de versión nueva:** al arrancar y una vez al día, el servidor consulta la última versión publicada en GitHub. Si hay una más nueva, la página muestra una barra con «Ver novedades», que se puede descartar.
  - Es una consulta anónima a GitHub que no envía datos tuyos. Usa tu proxy y tu CA si están configurados.
  - Se desactiva con `UPDATE_CHECK=0`.
  - La versión instalada se ve en ☰ → Estado y con `python server.py --version`.
- **Respaldo de la configuración** (☰ → 💾 Respaldo): descarga un archivo con tus reglas, plantillas, configuración de Jira y pestañas, y si quieres las preferencias del dispositivo. Sirve para pasarlo a otro PC o guardar una copia.
  - **No incluye secretos:** ni el token, ni las sesiones de los dispositivos, ni el 2FA, ni la auditoría, ni el historial.
  - Al importar eliges qué secciones traer y si agregarlas a lo actual o reemplazarlo. Todo se valida igual que si lo crearas a mano.
  - Las pestañas solo se agregan, y las carpetas que no existen en ese PC se omiten con aviso.
- **Buscar en la conversación** (🔍 o Ctrl+F / ⌘+F): busca en el historial de la pestaña abierta, sin importar mayúsculas ni tildes. Resalta cada coincidencia y muestra cuántas hay. Con ▲ ▼, o con Enter y Mayús+Enter, saltas entre ellas, partiendo por la más reciente. La búsqueda se hace en tu navegador: no se envía nada al servidor.
- **Exportar a Markdown** (☰ → 📝): descarga o copia la conversación de la pestaña abierta como un `.md`, por ejemplo para pegarla en un ticket o en la documentación.
  - Incluye tus mensajes, las respuestas de Claude con su formato, y el resumen de cada tarea (duración, costo y tokens).
  - Opcionalmente incluye las herramientas usadas y las decisiones con lo que respondiste.
  - Las capturas no se incluyen, solo se indica cuántas había.
  - La auditoría registra que se exportó, pero no el contenido.
- **Límite de gasto mensual** (☰ → 💲 Uso): defines un monto en US$ por mes y el % al que quieres un aviso (80 % por defecto).
  - Al acercarte aparece una barra arriba, en todos tus dispositivos.
  - Al llegar al límite **no se corta la tarea en curso**: solo dejan de iniciarse tareas nuevas, también las que estaban en cola. Puedes subir el límite o elegir «Continuar este mes»; el mes siguiente vuelve a aplicarse.
  - Cuenta desde el día 1 del mes con el costo estimado de Claude Code. Con un gateway corporativo el cobro real puede ser distinto.
  - El valor inicial se define con `SPEND_LIMIT_USD` (0 = sin límite) y `SPEND_WARN_PCT`. Se incluye en el respaldo, y la auditoría registra los cambios y las tareas bloqueadas.
- **Resumen de cambios** al terminar cada tarea: una tarjeta 📝 con los archivos que cambió Claude (nuevos, modificados y borrados) y cuántas líneas cambiaron. Al tocar un archivo ves su diff, en el mismo formato que las tarjetas de edición. Así revisas desde el celular antes de hacer commit.
  - En un repositorio git aparece todo lo que cambió durante la tarea, también lo hecho con comandos (un formateador, `sed`, `npm install`…). Los cambios que ya tenías antes de la tarea no se muestran. Solo se lee el repositorio, nunca se modifica.
  - Fuera de git aparecen los archivos editados con las herramientas de edición de Claude.
  - Los archivos binarios o muy grandes aparecen sin diff. Se desactiva con `CHANGES_SUMMARY=0`.
- **Diff en las tarjetas de edición:** antes de aprobar un `Edit`, `MultiEdit` o `Write` ves el cambio como en GitHub: líneas quitadas en rojo, agregadas en verde, con números de línea reales y contexto, leídos del archivo en tu PC. Los cambios largos aparecen plegados con «Ver todo». Si no se puede leer el archivo (por ejemplo, porque es binario o muy grande), se muestra igual el antes y el después, sin números de línea.
- **Rechazar con comentario:** con «💬 Rechazar con comentario» le explicas a Claude qué hacer distinto (por ejemplo, «usa `make test` en vez de `pytest`»). Claude recibe el motivo y propone otra forma de continuar, en vez de detenerse. En las preguntas, «✏️ Otra respuesta» permite escribir una respuesta libre en vez de elegir una opción. Todos tus dispositivos ven el comentario en la tarjeta.
- **Sesiones en paralelo** (pestañas bajo la barra superior): por ejemplo una por proyecto o por ticket, para que Claude trabaje en una mientras revisas otra.
  - Cada sesión tiene su propio Claude Code, carpeta, modo de permisos, historial y decisiones. ⏹ y el modo afectan solo a la pestaña abierta.
  - Las pestañas muestran ◆ cuando esperan una decisión, un punto pulsante mientras trabajan y • cuando hay novedades que no has visto.
  - **＋** crea una sesión (nombre y carpeta). Tocando la pestaña abierta la renombras o la cierras; al cerrarla se detiene su tarea y se borra su historial.
  - Cada dispositivo recuerda su pestaña abierta: puedes ver una en el PC y otra en el celular.
  - Las notificaciones llevan el nombre de la sesión, y al tocarlas se abre esa pestaña. La lectura en voz alta solo lee la pestaña abierta y avisa las decisiones de las demás.
  - Hasta 6 sesiones (`MAX_SESSIONS`). Cada una mantiene un proceso de Claude Code abierto. Las reglas, plantillas, Jira y la seguridad son comunes a todas.
- Menú con: conversación nueva, retomar una conversación anterior (también las de la terminal), cambio de carpeta, estado y detener. Todo se aplica a la pestaña abierta.
- Si cierras el navegador o se corta el Wi-Fi, al volver ves el historial y las decisiones pendientes.
- **Historial persistente:** el chat se guarda en tu PC (`~/.claude-remoto-history.json`, últimos 400 eventos) y se recupera al reiniciar el servidor. Las decisiones que quedaron sin responder aparecen como «⚠️ Expirada», porque Claude ya no las espera. Se borra desde ☰ → 🔐 Seguridad, o se desactiva con `HISTORY_PERSIST=0`.
- **Varios dispositivos en sincronía:** puedes tener abierta la misma sesión en el PC y en el celular. Lo que haces en uno, incluido aprobar o responder, aparece al instante en el otro, sin refrescar:
  - Un latido cada 10 s detecta conexiones caídas en silencio (pantalla bloqueada, cambio de Wi-Fi) y reconecta solo.
  - Al volver a la pestaña o recuperar la red, la página se resincroniza.
  - Si un proxy o antivirus retiene la conexión en vivo, la página cambia sola a modo *sondeo* (long-polling).
  - Cada evento tiene número, así que nada se duplica ni se pierde al reconectar.
  - El punto de la barra superior indica el estado: 🟢 conectado, 🟠 trabajando, 🔴 reconectando. Al tocarlo se ve si está en tiempo real o en sondeo.
- **Indicador de trabajo** mientras Claude trabaja: un símbolo animado (✢ ✳ ✶ ✻ ✽), un verbo con brillo que rota cada 3 s, el tiempo transcurrido y la herramienta en uso. Cuando espera una decisión cambia a «◆ Esperando tu decisión…»; al tocarlo te lleva a la tarjeta. Se ve igual en todos los dispositivos y respeta la opción de *reducir movimiento* del sistema.
- **Alertas de decisión:** cuando Claude pide aprobar, rechazar o responder:
  - suena una alerta, se repite cada 30 s mientras siga pendiente y el teléfono vibra;
  - aparece una **notificación del sistema** con botones **Aprobar / Rechazar**, para decidir sin abrir la app; las preguntas abren directo la tarjeta;
  - al decidir en cualquier dispositivo, la notificación desaparece de todos;
  - la pantalla se mantiene encendida mientras Claude trabaja, para que el teléfono no congele la página.
- **Instrucciones por voz** (🎤 junto a Enviar): dictas la tarea y el texto aparece en el cuadro para revisarlo antes de enviar. Con Whisper la voz se transcribe **en tu PC**, sin salir de tu red. Ver [Voz](#instrucciones-por-voz).
- **Adjuntar capturas** (📎 junto al cuadro): hasta 5 imágenes por tarea, por ejemplo un error en pantalla o un diseño.
  - En el celular eliges cámara, fotos o archivos. En el PC también puedes pegar una captura (Ctrl+V) o arrastrarla.
  - El navegador las reduce a un máximo de 2000 px antes de enviarlas.
  - Van directo a Claude, sin guardarse en tu PC ni en el proyecto. En el chat queda solo una miniatura, visible en todos tus dispositivos.
  - Se puede enviar solo la imagen, sin texto.
- **Comandos «/» con autocompletado:** al escribir `/` aparece la lista de comandos que ofrece tu Claude Code, con descripción y argumentos: los integrados (como `/compact` o `/context`), los de tu proyecto en `.claude/commands`, las skills y los prompts de tus servidores MCP. Se filtra mientras escribes. En el PC se elige con ↑/↓ y Enter o Tab, y en el celular tocando. La lista se pide a Claude Code la primera vez y otra vez al cambiar de carpeta. Algunos comandos interactivos de la terminal pueden no funcionar fuera de ella.
- **Enlaces a Jira** (☰ → 🎫 Jira): las claves de tickets del chat, como `ABC-123`, se convierten en enlaces que abren el ticket en una pestaña nueva.
  - Configuras una vez la dirección (`https://tuempresa.atlassian.net`, o `JIRA_URL` en `.env`) y aplica en todos tus dispositivos.
  - Para evitar falsos positivos se ignoran `UTF-8`, `SHA-256`, `ISO-8601` y similares. Puedes limitar los enlaces a tus proyectos (`ABC, DEV` o `JIRA_PROJECTS`).
  - No se enlaza dentro de bloques de código, y el enlace no envía a Jira la dirección de tu servidor local.
- **Plantillas** (⚡): botones sobre el cuadro de texto con instrucciones frecuentes, iguales en todos tus dispositivos.
  - Las marcadas con ➤ se envían al tocarlas, por ejemplo «Correr pruebas» o «Estado de git».
  - Las que tienen un campo, como `Revisa el ticket {ticket}…`, se insertan con `{ticket}` seleccionado para escribirlo o dictarlo.
  - Se crean, editan, ordenan y borran en ☰ → ⚡ Plantillas. Vienen 5 de ejemplo.
- **Respuestas habladas** (🔇/🔊 en la barra superior): con el interruptor activo, el dispositivo lee en voz alta las respuestas de Claude y avisa cuando necesita una decisión. Está apagado por defecto y se activa por dispositivo. Usa solo voces locales, así que el texto no sale de tu red.
- **Aviso al terminar tareas largas:** cuando una tarea dura más de 30 s (configurable: siempre, 30 s, 1, 2 o 5 min, o nunca), suena un tono distinto al de las decisiones y llega la notificación «✅ Claude terminó» (o «⚠️ terminó con error») con su duración. La notificación se cierra sola al volver a la app.

  Se configura en **☰ → 🔔 Alertas**. Ver [Notificaciones en el teléfono](#notificaciones-en-el-teléfono).

## Instalación
```bash
mkdir -p ~/claude-remoto && cd ~/claude-remoto
# o: git clone https://github.com/cesar-villalobos/claude-code-remoto.git ~/claude-remoto
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # solo claude-agent-sdk, python-dotenv y qrcode
cp .env.example .env && chmod 600 .env   # ajusta CLAUDE_WORKDIR y ALLOWED_ROOTS
```

**Servidores MCP:** Claude Remoto usa los que ya tienes configurados en Claude Code. Para agregar uno, se hace como siempre:
```bash
claude mcp add --transport http --scope user <nombre> <url>
claude    # /mcp → <nombre> → autenticar, si lo pide
```
También puedes declarar servidores solo para Claude Remoto con `MCP_SERVERS` en `.env` (JSON, ver `.env.example`). En las tarjetas, cada herramienta MCP aparece como «🧩 servidor · herramienta», y con las reglas puedes aprobar o bloquear un servidor completo (por ejemplo `mcp__miservidor__*`).

## Uso
```bash
python server.py
```
La terminal muestra la dirección (y un QR) con el token incluido, por ejemplo:
```
http://192.168.1.20:8765/?token=Xk3…
```
Ábrela en el celular, conectado a **la misma red Wi-Fi**. El token queda guardado en una cookie, así que las siguientes veces basta con `http://192.168.1.20:8765`.

**En el propio PC, usa `http://127.0.0.1:8765`** (la terminal también muestra esa dirección). En equipos corporativos, el navegador suele enviar las direcciones de la red, como `192.168.x.x`, por el proxy de la empresa, y el proxy retrasa las actualizaciones. `127.0.0.1` va directo.

Consejo: en el celular usa *Agregar a pantalla de inicio* para abrirla como si fuera una app.

### Arranque automático
Para que el servidor se inicie solo cada vez que inicias sesión en el PC, sin abrir una terminal:
```bash
python server.py --instalar-inicio    # con el mismo Python o entorno virtual que usas a diario
```
Usa lo propio de cada sistema y **no pide permisos de administrador**:

| Sistema | Cómo | Dónde |
|---|---|---|
| Windows | Tarea del Programador de tareas, solo para tu usuario y sin ventana | «Programador de tareas» → `ClaudeRemoto` |
| macOS | LaunchAgent | `~/Library/LaunchAgents/com.claude-remoto.server.plist` |
| Linux | Servicio de usuario de systemd | `~/.config/systemd/user/claude-remoto.service` |

Detalles:
- Si el servidor se cae, se reinicia solo.
- El registro queda en `~/.claude-remoto.log`, con permisos solo para tu usuario y rotación automática. **El token no se escribe en el registro.**
- Para ver el enlace con token y el QR cuando quieras conectar un dispositivo nuevo: `python server.py --mostrar-acceso`.
- `python server.py --estado-inicio` muestra si está instalado y corriendo. `python server.py --quitar-inicio` lo desactiva.
- Si abres `python server.py` a mano mientras el automático está corriendo, te avisa que el puerto ya está en uso.
- En Linux, para que arranque aunque no inicies sesión: `loginctl enable-linger $USER`.

> **Importante:** el arranque automático no ve las variables que defines en la terminal (por ejemplo, las del gateway o el proxy en tu `~/.bashrc` o `~/.zshrc`). Al instalar, te avisa cuáles faltan en `.env`, mostrando solo los nombres: cópialas a `.env` y reinicia el servicio. Lo que está en la configuración de Claude Code (`~/.claude/settings.json`) sí funciona igual.

## Instrucciones por voz
Toca 🎤, habla y vuelve a tocar para terminar (máximo 2 minutos). El texto se agrega al cuadro para que lo revises antes de enviarlo.

Hay dos formas de transcribir:

| | Whisper en tu PC (recomendado) | Dictado del navegador |
|---|---|---|
| Dónde se procesa la voz | En tu PC, sin internet | En servidores de Google (Chrome) o Apple (Safari) |
| Instalación | `pip install -r requirements-voz.txt` | Nada |
| Velocidad | Unos segundos al terminar de hablar | Texto en vivo mientras hablas |

Por defecto se usa Whisper si está instalado. Si no lo está, el botón ofrece el dictado del navegador y **avisa antes** de que la voz sale a internet. En ☰ → 🎤 Voz eliges, por dispositivo, «Automático», «Solo Whisper» (nunca usa el navegador) o «Navegador».

**Instalar Whisper:**
```bash
pip install -r requirements-voz.txt
```
La primera vez que dictes, se descarga el modelo (`base`, ~150 MB). Después funciona sin internet. Si el proxy de tu empresa bloquea la descarga, baja el modelo en otra red y pon su carpeta en `WHISPER_MODEL`.

**Requisitos:**
- El micrófono del navegador exige HTTPS (o `127.0.0.1` en el propio PC). Ver [Notificaciones en el teléfono](#notificaciones-en-el-teléfono) para configurar los certificados.
- En el iPhone, Safari pide permiso para el micrófono la primera vez.

**Configuración** (en `.env`, todas opcionales): `VOICE_ENGINE` (`auto`, `whisper`, `browser` u `off`), `WHISPER_MODEL` (`tiny`, `base`, `small`…; `small` entiende mejor el español pero es más lento), `WHISPER_LANGUAGE` (`es`), `VOICE_MAX_MB` (8).

La auditoría registra cada dictado (tamaño y duración), pero no el texto ni el audio. El audio se borra apenas se transcribe.

### Escuchar las respuestas
El botón 🔇/🔊 de la barra superior activa la lectura en voz alta **solo en ese dispositivo**. Está apagado por defecto: puedes tenerlo encendido en el celular mientras manejas o caminas y apagado en el PC.

- Lee las respuestas nuevas de Claude, sin los símbolos de Markdown. Los bloques de código se anuncian como «bloque de código», en vez de leerse.
- Si una respuesta es muy larga, lee el comienzo y avisa que continúa en pantalla.
- Con «Avisar también las decisiones», dice «Claude necesita tu aprobación» o «Claude tiene una pregunta» cuando aparece una tarjeta.
- Al apagar el interruptor o enviar una tarea nueva, deja de hablar al instante.
- Al recargar la página no relee el historial.

Usa la síntesis de voz del propio dispositivo, sin instalar nada. **Solo se usan voces locales**: Chrome también ofrece voces en línea («Google español») que envían el texto a servidores de Google, y esas se descartan. La voz, la velocidad y los avisos se ajustan en ☰ → 🎤 Voz.

En el iPhone, Safari solo habla mientras la página está abierta en pantalla. Con «Pantalla encendida mientras trabaja» (☰ → 🔔 Alertas), sigue leyendo mientras Claude trabaja.

## Notificaciones en el teléfono
El **sonido** funciona siempre con la página abierta. La primera vez, toca **Activar** en la barra superior: los navegadores solo permiten sonido después de un toque.

Las **notificaciones del sistema** exigen HTTPS:
- **En el PC:** abre `http://127.0.0.1:8765`. Los navegadores tratan `127.0.0.1` como seguro, así que ahí funcionan sin hacer nada más.
- **En el teléfono:** hace falta HTTPS con un certificado en el que el teléfono confíe. Se configura una sola vez:

### 1. Generar certificados (en el PC)
```bash
./gen-certs.sh            # detecta tu IP; o indícala: ./gen-certs.sh 192.168.1.20
```
Luego agrega a `.env` estas líneas y reinicia `server.py`:
```
TLS_CERT=./certs/server.crt
TLS_KEY=./certs/server.key
CA_CERT=./certs/ca.crt
```
La CA (`certs/ca.key`) solo existe en tu PC y nunca sale de ahí. Si cambia la IP del PC, vuelve a ejecutar el script: la CA se reutiliza y no hay que reinstalarla.

### 2. Instalar la CA en el teléfono
Abre `https://<IP-del-PC>:8765/ca.crt` en el teléfono. Esa única vez, acepta la advertencia de certificado.

**Android**
1. El archivo se descarga.
2. Ve a *Ajustes → Seguridad → Más ajustes de seguridad → Cifrado y credenciales → Instalar un certificado → Certificado de CA*. La ruta exacta varía según el fabricante; busca «certificado» en Ajustes.
3. Elige `claude-remoto-ca.crt`.
4. Abre `https://<IP-del-PC>:8765`: ya no aparece la advertencia. Toca **Activar** y permite las notificaciones.
5. Opcional: en el menú de Chrome, *Agregar a pantalla principal*, para abrirla como app.

**iPhone (iOS 16.4 o posterior)**
1. Safari ofrece descargar un perfil: acepta.
2. Ve a *Ajustes → General → VPN y gestión de dispositivos* e instala el perfil «Claude Remoto CA local».
3. Ve a *Ajustes → General → Información → Ajustes de confianza de certificados* y activa la confianza total para esa CA.
4. En Safari, toca *Compartir → Agregar a inicio*. En iPhone, las notificaciones web **solo** funcionan como app de pantalla de inicio.
5. Abre la app desde el ícono, toca **Activar** y permite las notificaciones.

Si es un teléfono corporativo con gestión de dispositivos (MDM), puede que no te deje instalar la CA.

### Limitaciones de las notificaciones normales
- Las notificaciones normales se generan en tu red local, sin pasar por servidores de Google ni Apple. La contrapartida es que **solo funcionan mientras el navegador mantiene viva la página**.
- Con el teléfono bloqueado mucho rato, el sistema congela la página y los avisos no llegan hasta que vuelves a abrirla. Para evitarlo, deja activada **Pantalla encendida mientras trabaja** (☰ → 🔔 Alertas) o usa los avisos con la app cerrada.

### Avisos con la app cerrada (Web Push, opcional)
En ☰ → 🔔 Alertas → **📲 Avisos con la app cerrada**, actívalos en cada dispositivo. Llegan aunque cierres la app o bloquees el teléfono: «Claude necesita tu decisión» y «Claude terminó» (tareas de más de `PUSH_DONE_AFTER` segundos, 30 por defecto).

**Qué sale a internet:** para llegar a un teléfono con la app cerrada, el aviso tiene que pasar por el servicio de notificaciones del navegador (Apple, Google, Mozilla o Microsoft). Por eso:
- Están **apagados** hasta que los actives en cada dispositivo, y se desactivan para todo el PC con `PUSH=0`.
- El aviso va **cifrado de punta a punta** (RFC 8291): el servicio de push no puede leerlo.
- Además es **genérico**: solo dice que hay una decisión o que terminó una tarea. No incluye comandos, archivos, textos, el nombre de la pestaña ni la carpeta. El detalle se ve al abrir la app, ya en tu red local. Tampoco trae botones de Aprobar/Rechazar, porque no se ve qué se aprueba.
- El PC solo envía a los servicios de push conocidos, nunca a otras direcciones. Usa `CORP_PROXY` y `CORP_CA_BUNDLE` si están configurados.
- Si el dispositivo tiene la app abierta, avisa la página y el push no se envía.
- Al cerrar un dispositivo desde 🔐 Seguridad, deja de recibir avisos. La auditoría registra cuándo se activan, desactivan y prueban.

Requisitos:
- HTTPS con la CA local instalada en el teléfono (ver arriba).
- El PC necesita salida a internet, directa o por el proxy. Si la red de la oficina la bloquea, los avisos no llegan y todo lo demás sigue funcionando.
- En iPhone: iOS 16.4 o más nuevo, y la app agregada a la pantalla de inicio y abierta desde ahí.
- En iPhone, las notificaciones no tienen botones de Aprobar/Rechazar: al tocarlas se abre la tarjeta.
- Por defecto, la notificación muestra solo el tipo de acción y el archivo, no el comando completo, porque es visible en la pantalla bloqueada. Se puede cambiar en «Detalle en la notificación».

## Acceso remoto con VPN
Para usarlo fuera de casa, usa una Raspberry Pi (o un PC) como puente de Tailscale hacia tu red local. Desde el celular abres la misma dirección privada de siempre y el QR del arranque sirve igual. No se abre ningún puerto en el router: el servidor solo acepta conexiones de redes privadas. Guías paso a paso:
- **[Con Raspberry Pi](docs/vpn-tailscale.md)** (recomendado)
- **[Con un PC o Mac](docs/vpn-tailscale-pc-mac.md)** (Windows, macOS, Linux), como puente en casa o como cliente desde un notebook

## Estructura del código
`server.py` es solo el punto de entrada; el código está en el paquete `claude_remoto/`, ordenado en capas (cada módulo importa solo de los de arriba):

| Módulo | Qué hace |
|---|---|
| `config.py` | Variables de entorno y `.env` |
| `state.py` | Estado en disco: token, carpeta, sesión de Claude, modo, reglas y 2FA |
| `audit.py` | Registro de auditoría (JSONL) y su búsqueda |
| `hub.py` | Eventos hacia los navegadores e historial persistente |
| `sessions.py` | Sesiones de cada dispositivo |
| `totp.py` | Segundo factor (RFC 6238) |
| `rules.py` | Reglas de aprobación por patrón |
| `diff.py` | Diff de las ediciones para las tarjetas |
| `decisions.py` | Decisiones pendientes y su resolución |
| `agent.py` | Sesiones de Claude Code (una por pestaña): permisos, hooks, streaming |
| `claude_sessions.py` | Sesiones guardadas de Claude Code y carpetas permitidas |
| `auth.py` | Filtro de IP, límites de intentos, token y cookies |
| `api.py` | API que usa la interfaz (`/api/send`, `/api/decide`, `/api/cmd`) |
| `web.py` | Servidor HTTP: login, páginas, SSE y long-polling |
| `templates.py` | Plantillas de instrucciones frecuentes |
| `jira.py` | Configuración de los enlaces a Jira |
| `usage.py` | Registro de costo y uso por tarea |
| `updates.py` | Aviso de versión nueva (última release en GitHub) |
| `export_md.py` | Conversación a Markdown |
| `budget.py` | Límite de gasto mensual |
| `changes.py` | Resumen de los archivos que cambió cada tarea |
| `push.py` | Web Push: cifrado, VAPID, suscripciones y envío de avisos genéricos |
| `backup.py` | Exportar e importar la configuración |
| `attachments.py` | Validación de capturas adjuntas y bloques de imagen para Claude |
| `voice.py` | Transcripción local de la voz con Whisper |
| `autostart.py` | Arranque automático (Programador de tareas, LaunchAgent, systemd) |
| `app.py` | Arranque, banner con QR y opciones de línea de comandos |

La interfaz está en `ui.html` (una sola página, sin dependencias) y `sw.js` (notificaciones).

## Pruebas
El proyecto tiene pruebas automáticas que usan un **Claude Code simulado** (`tests/fake_claude.py`), así que no necesitan Claude Code, el gateway ni conexión:

| Archivo | Qué prueba |
|---|---|
| `test_unit.py` | Filtro de IPs (solo redes privadas), límites de login, historial |
| `test_api.py` | Login, CSRF, flujo completo de permisos y preguntas, detener, archivos públicos, SSE |
| `test_sesiones.py` | Sesiones en paralelo: dos tareas a la vez, decisiones y detener por sesión, crear, renombrar, cerrar, límite, persistencia y migración |
| `test_tls.py` | Certificados de `gen-certs.sh` y HTTPS con la CA local |
| `test_static.py` | Sintaxis del JavaScript, del service worker y del script de certificados |
| `test_arranque.py` | Arranque automático en Windows, macOS y Linux, aviso de variables, registro sin token, `--mostrar-acceso` y puerto ocupado |
| `test_capturas.py` | Capturas: envío a Claude, solo miniaturas en el historial y validación (tipo, contenido, tamaño, cantidad) |
| `test_comandos.py` | Comandos «/»: lista de Claude Code, normalización y consulta al cambiar de carpeta |
| `test_cola.py` | Cola de mensajes: orden, quitar, detener, límite, por pestaña y reinicio |
| `test_modelo.py` | Modelo por pestaña: lista, cambio en caliente, persistencia y lista configurable |
| `test_costo.py` | Costo y uso: registro sin el texto de la tarea, totales por período, pestaña y modelo |
| `test_version.py` | Aviso de versión nueva: comparación, estado, desactivación y sin conexión |
| `test_respaldo.py` | Respaldo: exportar sin secretos, importar en otro equipo, repetir sin duplicar, reemplazar y validar |
| `test_exportar.py` | Exportar a Markdown: formato, opciones, escape de código y solo la pestaña pedida |
| `test_limite.py` | Límite de gasto: aviso, pausa de tareas nuevas y de la cola, continuar este mes, validación y mes en curso |
| `test_cambios.py` | Resumen de cambios: git (también lo hecho con comandos y sin los cambios previos), fuera de git, binarios y desactivado |
| `test_push.py` | Web Push: cifrado con el vector del RFC 8291, firma VAPID, solo servicios de push conocidos, aviso genérico y a quién se envía |
| `test_jira.py` | Enlaces a Jira: configuración, validación y valores de `.env` |
| `test_plantillas.py` | Plantillas: ejemplos, edición, validación, orden, límite y persistencia |
| `test_voz.py` | Voz: motor según la configuración, transcripción con Whisper simulado, límites y protección del audio |
| `test_totp.py` | Segundo factor: vectores del RFC 6238, activación, login con formulario y con enlace/QR, reutilización de códigos, recuperación |
| `test_browser.py` | En Chromium real: sincronización entre pestañas (una con SSE bloqueado), indicador de trabajo, sonido, notificaciones con botones, borrar historial, activar 2FA, dictado por voz, respuestas habladas, plantillas entre dispositivos, autocompletado de comandos, enlaces a Jira, capturas adjuntas, sesiones en paralelo y diálogos en pantalla baja |

Para correrlas en tu equipo:
```bash
pip install -r requirements-dev.txt
python -m playwright install chromium     # una vez
pytest                                    # todas (≈3 min)
pytest -m "not browser"                   # solo las rápidas (≈30 s)
```
Las pruebas no leen tu `.env` ni usan tu proxy.

En GitHub se ejecutan solas en cada push a `main` y `develop` y en cada pull request, incluidos los que vengan de forks (`.github/workflows/pruebas.yml`). Para probar una rama sin abrir un PR: pestaña *Actions* → *Pruebas* → **Run workflow**.

## Seguridad
- **Token de acceso** de 32 caracteres aleatorios. Tras 10 intentos fallidos, esa IP queda bloqueada 10 minutos.
- **Solo redes privadas** (`ALLOWED_NETS`): las conexiones desde IPs públicas se rechazan siempre; desde fuera de casa se entra por la [VPN](#acceso-remoto-con-vpn). Para restringir solo a tu casa: `ALLOWED_NETS=192.168.1.0/24`.
- **Límite de intentos:** además del límite por IP, 30 fallos sumando todas las IPs (`GLOBAL_FAIL_LIMIT`) bloquean el login durante 15 minutos. Las sesiones ya abiertas siguen funcionando.
- **Cookie** `HttpOnly` y `SameSite=Strict`, con protección contra peticiones de otros sitios (CSRF) y cabeceras de seguridad.
- **Sesión por dispositivo** (☰ → 🔐 Seguridad): ves cada dispositivo conectado con su IP y última actividad, y puedes cerrar uno o todos los demás. El dispositivo se desconecta al instante. Cada sesión dura `SESSION_DAYS` días (30 por defecto) y en el PC solo se guarda su huella (SHA-256), nunca la cookie.
- **Rotar el token** desde el mismo panel: genera uno nuevo y cierra las demás sesiones. Si definiste `ACCESS_TOKEN` en `.env`, cámbialo ahí.
- **Auditoría:** cada inicio de sesión (correcto o fallido), tarea, decisión, regla y comando que ejecuta Claude queda en `~/.claude-remoto-audit.jsonl`, solo legible por tu usuario. Se consulta y filtra desde ☰ → 🔐 Seguridad → *Ver registro*, y se puede descargar. Se rota al llegar a `AUDIT_MAX_MB` (5 MB).
- **Web Push** (opcional, apagado por defecto en cada dispositivo): es lo único que sale a internet, cifrado y sin detalles de la tarea. Ver [Avisos con la app cerrada](#avisos-con-la-app-cerrada-web-push-opcional).
- **Detenerlo:** con Ctrl+C se cierra el puerto y deja de ser accesible.

### Segundo factor (2FA)
Opcional, recomendado si usas la VPN o compartes la red con otras personas. Con 2FA, para entrar hace falta el token **y** un código de 6 dígitos de una app de autenticación (Google Authenticator, Microsoft Authenticator, Aegis, 1Password…). Funciona sin internet: el código se calcula en el teléfono con la hora.

1. ☰ → 🔐 Seguridad → **Activar 2FA**.
2. Escanea el QR con la app, o escribe la clave a mano. En el teléfono también puedes tocar «abrir en la app».
3. Escribe el código que muestra la app. Las sesiones de los demás dispositivos se cierran y deberán entrar con código.

Detalles:
- El enlace o QR con el token ya no da acceso directo: abre la página y pide el código.
- Cada código sirve una sola vez, y la hora del teléfono y del PC debe estar bien (se tolera ±30 s).
- Para desactivarlo se pide un código actual.
- **Si pierdes el teléfono**, en el PC detén el servidor y ejecuta `python server.py --desactivar-2fa`. Luego vuelve a activarlo con el teléfono nuevo.

### Si estás en la red de la oficina
- La red de la oficina también es "local": tus compañeros estarían en la misma red. Usa HTTPS y restringe `ALLOWED_NETS` a tu subred, o úsalo solo desde casa.
- Muchos equipos corporativos bloquean las conexiones entrantes con su firewall o con software de seguridad (EDR). Si el celular no logra conectarse, probablemente sea eso. Abrir un puerto puede requerir aprobación de TI.
- Algunas redes Wi-Fi (de invitados o corporativas) tienen **aislamiento de clientes**: los equipos no pueden verse entre sí. En ese caso usa el hotspot del celular o la red de tu casa.

## Problemas comunes
| Síntoma | Solución |
|---|---|
| El celular no carga la página | Confirma que ambos estén en la misma red. Revisa el firewall del PC (macOS: Ajustes → Red → Firewall; Windows: permite Python en redes privadas) |
| "Rechazada conexión… fuera de ALLOWED_NETS" en la terminal | Agrega tu red a `ALLOWED_NETS` |
| Un dispositivo no se actualiza | Revisa el punto de la barra superior. Si está 🔴, se reconecta solo en unos segundos. En el PC usa `127.0.0.1` en vez de la IP de la red |
| Se queda en ⏳ y no avanza | Probablemente hay una tarjeta de decisión esperando: baja hasta el final del chat |
| `SSL: CERTIFICATE_VERIFY_FAILED` en las respuestas de Claude | Configura `CORP_CA_BUNDLE` (es la conexión de Claude hacia el gateway, no la del celular) |

## Licencia
[MIT](LICENSE). Úsalo bajo tu propia responsabilidad: ver el [aviso de seguridad](#claude-remoto-red-local) y [SECURITY.md](SECURITY.md).

Proyecto independiente, sin afiliación con Anthropic. «Claude» y «Claude Code» son marcas de Anthropic.
