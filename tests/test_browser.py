"""Pruebas de punta a punta en un navegador real (Playwright/Chromium)."""

import pytest

from mando import __version__
from conftest import TOKEN

pytestmark = pytest.mark.browser

# Simula pestaña en segundo plano (en headless todas parecen visibles) y cuenta las notas tocadas.
INIT = """(()=>{
  window.__hidden=false;
  Object.defineProperty(document,'visibilityState',{get:()=>window.__hidden?'hidden':'visible'});
  document.hasFocus=()=>!window.__hidden;
  window.__notes=0;
  const O=window.AudioContext;
  if(O){window.AudioContext=class extends O{createOscillator(){const o=super.createOscillator();
    const s=o.start.bind(o);o.start=(...a)=>{window.__notes++;return s(...a)};return o}}}
})()"""


def new_context(browser, srv, viewport=None, **kw):
    ctx = browser.new_context(bypass_csp=True, viewport=viewport or {"width": 390, "height": 740}, **kw)
    ctx.add_init_script(INIT)
    return ctx


def log_texts(page):
    return [x.inner_text() for x in page.locator("#log > *").all()]


def test_sincronizacion_entre_tres_pestanas(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    bloq = ctx.new_page()                       # pestaña con el flujo SSE retenido (proxy que bufferea)
    bloq.route("**/events", lambda route: None)
    bloq.goto(f"{s.base}/")
    pc.wait_for_timeout(1000)

    pc.fill("#input", "tarea desde el PC")
    pc.click("#send")
    cel.wait_for_selector(".card .yes", timeout=8000)
    cel.click(".card .yes")                     # aprueba el celular…
    pc.wait_for_selector(".card.closed .result", timeout=5000)
    assert pc.locator(".card.closed .result").first.inner_text() == "✅ Aprobado"  # …y el PC lo ve

    pc.wait_for_selector(".card .opt", timeout=5000)
    pc.locator(".card .opt").nth(1).click()     # responde el PC…
    cel.wait_for_selector("text=Respuesta:", timeout=5000)   # …y el celular lo ve
    cel.wait_for_selector(".done", timeout=5000)

    # el celular pierde la red y se recupera
    ctx.set_offline(True)
    cel.wait_for_timeout(800)
    ctx.set_offline(False)
    cel.evaluate("window.dispatchEvent(new Event('online'))")

    # la pestaña bloqueada pasa a sondeo y recibe todo
    for _ in range(60):
        if "sondeo" in (bloq.get_attribute("#dot", "title") or ""):
            break
        bloq.wait_for_timeout(500)
    assert "sondeo" in bloq.get_attribute("#dot", "title")
    bloq.wait_for_selector(".done", timeout=10000)

    pc.wait_for_timeout(1500)
    a, b, c = log_texts(pc), log_texts(cel), log_texts(bloq)
    # tarea, texto, permiso, texto, pregunta, texto, «Listo»
    assert a == b == c and len(a) == 7, (len(a), len(b), len(c))
    ctx.close()


def test_indicador_de_trabajo(browser, make_server):
    s = make_server("slow")
    ctx = new_context(browser, s)
    cel, pc = ctx.new_page(), ctx.new_page()
    cel.goto(f"{s.base}/?token={TOKEN}")
    pc.goto(f"{s.base}/")
    cel.wait_for_timeout(800)
    assert cel.locator("#working").is_hidden()

    pc.fill("#input", "arregla el login")
    pc.click("#send")
    cel.wait_for_selector("#working:not([hidden])", timeout=5000)
    glyphs = set()
    for _ in range(12):
        glyphs.add(cel.locator("#wkGlyph").inner_text())
        cel.wait_for_timeout(120)
    assert len(glyphs) >= 3, glyphs                      # el símbolo se anima

    cel.wait_for_selector("#wkAct:text('Read')", timeout=5000)
    assert cel.locator("#wkVerb").inner_text() == pc.locator("#wkVerb").inner_text()  # mismo verbo

    cel.wait_for_selector("#working.waiting", timeout=10000)
    assert cel.locator("#wkVerb").inner_text() == "Esperando tu decisión…"
    pc.click(".card .yes")
    cel.wait_for_selector("#working:not(.waiting)", timeout=4000)
    cel.wait_for_selector("#working[hidden]", state="attached", timeout=8000)  # al terminar se oculta
    ctx.close()


def test_alertas_y_notificacion_con_botones(browser, make_server):
    s = make_server("slow", FAKE_DELAY="0.5")
    ctx = new_context(browser, s, permissions=["notifications"])
    cel = ctx.new_page()
    cel.goto(f"{s.base}/?token={TOKEN}")
    cel.wait_for_timeout(800)
    assert cel.locator("#alertBar").is_visible()
    cel.click("#alertOn")
    cel.wait_for_timeout(600)
    assert cel.evaluate("window.__notes") == 3          # sonido de confirmación

    pc = ctx.new_page()
    pc.goto(f"{s.base}/")
    cel.evaluate("window.__hidden=true")                 # celular en segundo plano
    before = cel.evaluate("window.__notes")
    pc.fill("#input", "arregla el login")
    pc.click("#send")
    cel.wait_for_selector(".card .yes", timeout=10000)
    cel.wait_for_timeout(500)
    assert cel.evaluate("window.__notes") - before == 3  # suena al pedir permiso

    notes = []
    for _ in range(20):  # showNotification es asíncrono
        notes = cel.evaluate("navigator.serviceWorker.ready.then(r=>r.getNotifications())"
                             ".then(n=>n.map(x=>({t:x.title,b:x.body,a:x.actions.map(a=>a.action)})))")
        if notes:
            break
        cel.wait_for_timeout(250)
    assert notes == [{"t": "Claude pide permiso", "b": "✏️ Edit · src/auth/login.py", "a": ["y", "n"]}]

    # tocar «Aprobar» en la notificación (se simula el evento en el service worker)
    sw = ctx.service_workers[0]
    sw.evaluate("""async()=>{const ns=await self.registration.getNotifications();
        const ev=new NotificationEvent('notificationclick',{notification:ns[0],action:'y'});
        let p;ev.waitUntil=x=>{p=x};self.dispatchEvent(ev);await p}""")
    pc.wait_for_selector(".card.closed .result", timeout=5000)
    assert pc.locator(".card.closed .result").first.inner_text() == "✅ Aprobado"
    cel.wait_for_timeout(500)
    assert cel.evaluate("navigator.serviceWorker.ready.then(r=>r.getNotifications()).then(n=>n.length)") == 0
    ctx.close()


def test_rechazar_con_comentario_desde_la_interfaz(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    cel, pc = ctx.new_page(), ctx.new_page()
    cel.goto(f"{s.base}/?token={TOKEN}")
    pc.goto(f"{s.base}/")
    cel.wait_for_timeout(800)
    pc.fill("#input", "corre las pruebas")
    pc.click("#send")

    cel.wait_for_selector(".card .yes", timeout=8000)
    cel.click("text=💬 Rechazar con comentario")
    cel.click("text=❌ Rechazar y enviar")            # vacío: no envía
    assert cel.locator(".card .comment textarea").count() == 1
    cel.fill(".card .comment textarea", "usa make test en vez de pytest")
    cel.click("text=❌ Rechazar y enviar")

    # el PC ve el rechazo con el comentario, y Claude lo recibe
    pc.wait_for_selector(".card.closed .result", timeout=5000)
    assert pc.locator(".card.closed .result").first.inner_text() == "❌ Rechazado · «usa make test en vez de pytest»"
    pc.wait_for_selector("text=comentó: «usa make test en vez de pytest»", timeout=5000)

    # «Otra respuesta» en la pregunta
    pc.wait_for_selector(".card .opt", timeout=5000)
    pc.click("text=✏️ Otra respuesta")
    pc.fill(".card:not(.closed) .comment textarea", "feature/login")
    pc.keyboard.press("Control+Enter")
    cel.wait_for_selector("text=Respuesta: {'¿Qué rama?': 'feature/login'}", timeout=5000)
    assert cel.locator(".card.closed .result").nth(1).inner_text() == "→ «feature/login»"
    ctx.close()


def _prefs_script(done_after):
    return ("localStorage.setItem('cr_prefs', JSON.stringify({sound:true,notify:true,wake:false,"
            f"detail:false,asked:true,doneAfter:{done_after}}}))")


def _run_slow_task(browser, make_server, done_after):
    """Tarea lenta (≈3 s) con el celular en segundo plano; devuelve (notas tocadas, notificaciones)."""
    s = make_server("slow", FAKE_DELAY="0.3")
    ctx = new_context(browser, s, permissions=["notifications"])
    ctx.add_init_script(_prefs_script(done_after))
    cel = ctx.new_page()
    cel.goto(f"{s.base}/?token={TOKEN}")
    cel.wait_for_timeout(800)
    cel.click("#log")                                     # gesto que desbloquea el audio
    cel.evaluate("navigator.serviceWorker.ready")
    cel.evaluate("window.__hidden=true")                 # celular en segundo plano
    s.login()
    s.api("/api/send", {"text": "tarea larga"})
    perm, _ = s.wait_for(lambda e: e["type"] == "perm")
    cel.wait_for_timeout(500)
    notes_before_done = cel.evaluate("window.__notes")    # 3 del aviso de decisión
    s.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    s.wait_for(lambda e: e["type"] == "done")
    notes = []
    for _ in range(12):
        cel.wait_for_timeout(250)
        notes = cel.evaluate("navigator.serviceWorker.ready.then(r=>r.getNotifications({tag:'cr-done'}))"
                             ".then(n=>n.map(x=>({t:x.title,b:x.body})))")
        if notes:
            break
    played = cel.evaluate("window.__notes") - notes_before_done
    cel.evaluate("window.__hidden=false; document.dispatchEvent(new Event('visibilitychange'))")
    cel.wait_for_timeout(400)
    left = cel.evaluate("navigator.serviceWorker.ready.then(r=>r.getNotifications({tag:'cr-done'})).then(n=>n.length)")
    ctx.close()
    return played, notes, left


def test_aviso_al_terminar_tarea_larga(browser, make_server):
    played, notes, left = _run_slow_task(browser, make_server, done_after=0)
    assert played == 2, played                            # sonido de fin: dos notas
    assert len(notes) == 1 and notes[0]["t"] == "✅ Claude terminó"
    assert notes[0]["b"].startswith("Duró ") and "Toca para ver el resultado" in notes[0]["b"]
    assert left == 0                                      # se cierra al volver a la app


def test_sin_aviso_bajo_el_umbral(browser, make_server):
    played, notes, _ = _run_slow_task(browser, make_server, done_after=300)
    assert played == 0 and notes == []


def test_respuesta_en_tiempo_real_en_pantalla(browser, make_server):
    s = make_server("stream", FAKE_DELAY="1")
    ctx = new_context(browser, s)
    pc = ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    pc.wait_for_timeout(600)
    pc.fill("#input", "revisa el login")
    pc.click("#send")
    pc.wait_for_selector(".msg.streaming", timeout=5000)
    first = pc.locator(".msg.streaming").inner_text()
    cel = ctx.new_page()                              # se conecta a mitad de la respuesta
    cel.goto(f"{s.base}/")
    cel.wait_for_selector(".msg.streaming", timeout=5000)
    pc.wait_for_timeout(1200)
    later = pc.locator(".msg.streaming").inner_text()
    assert len(later) > len(first), (first, later)    # el texto crece
    assert pc.locator("#wkAct").inner_text() == "✍️ Escribiendo la respuesta…"
    pc.wait_for_selector(".done", timeout=10000)
    cel.wait_for_selector(".done", timeout=10000)
    for page in (pc, cel):                            # una sola burbuja, sin cursor, con el texto final
        assert page.locator(".msg.streaming").count() == 0
        bubbles = [t for t in log_texts(page) if t.startswith("Revisé el módulo")]
        assert bubbles == ["Revisé el módulo de login y encontré el problema:\n\nfaltaba validar el token."], bubbles
    ctx.close()


def test_diff_en_la_tarjeta(browser, make_server):
    s = make_server("edit")
    ctx = new_context(browser, s)
    pg = ctx.new_page()
    pg.goto(f"{s.base}/?token={TOKEN}")
    pg.wait_for_timeout(600)
    pg.fill("#input", "corrige total()")
    pg.click("#send")
    pg.wait_for_selector(".card .diff", timeout=8000)
    assert pg.locator(".card .diff-head").first.inner_text().startswith("+2 −1")
    deleted = pg.locator(".card .dl.del").first
    assert "return 42" in deleted.inner_text() and deleted.locator(".ln").first.inner_text() == "62"
    assert pg.locator(".card .dl.add").count() == 2
    assert pg.locator(".card .dl.hunk").first.inner_text().strip() == "@@ línea 59 @@"
    assert pg.locator(".card .diff-more").count() == 0               # diff corto: sin plegar
    pg.click(".card .yes")

    # archivo nuevo de 80 líneas: se muestra plegado con «Ver todo»
    pg.wait_for_selector(".card:not(.closed) .diff-more", timeout=8000)
    card = pg.locator(".card:not(.closed)")
    assert "Crear archivo" in card.locator("h3").inner_text()
    assert "archivo nuevo" in card.locator(".diff-head").inner_text()
    body = card.locator(".diff-body")
    assert "folded" in body.get_attribute("class")
    card.locator(".diff-more").click()
    assert "folded" not in body.get_attribute("class") and card.locator(".diff-more").inner_text() == "Ver menos"
    ctx.close()


def test_selector_de_modo_y_tarjeta_de_plan(browser, make_server):
    s = make_server("plan")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    pc.wait_for_timeout(800)
    assert pc.locator("#modeBtn").inner_text() == "🛡️ Preguntar"
    pc.click("#modeBtn")
    pc.click(".mode-opt:has-text('Solo planificar')")
    cel.wait_for_selector("#modeBtn.plan", timeout=4000)              # el otro dispositivo se actualiza
    assert cel.locator("#modeBtn").inner_text() == "📋 Solo planificar"

    pc.fill("#input", "arregla el login")
    pc.click("#send")
    cel.wait_for_selector(".plan-body", timeout=8000)
    assert cel.locator(".plan-body h3, .plan-body b").first.inner_text() == "Plan"
    assert "Agregar validación del token" in cel.locator(".plan-body").inner_text()
    cel.click("text=✏️ Aprobar y aceptar ediciones")
    pc.wait_for_selector(".card.closed .result", timeout=5000)
    assert pc.locator(".card.closed .result").first.inner_text() == "✅ Plan aprobado · aceptar ediciones"
    pc.wait_for_selector("#modeBtn.acceptEdits", timeout=5000)        # sale del modo plan
    cel.wait_for_selector("#modeBtn.acceptEdits", timeout=5000)
    ctx.close()


def test_crear_regla_desde_la_tarjeta(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pg = ctx.new_page()
    pg.goto(f"{s.base}/?token={TOKEN}")
    pg.wait_for_timeout(600)
    pg.fill("#input", "corre las pruebas")
    pg.click("#send")
    pg.wait_for_selector(".card .yes", timeout=8000)
    pg.click("text=⚖️ Crear regla para casos así")
    pg.wait_for_selector("#ruleForm")
    assert pg.input_value("#ruleForm [name=pattern]") == "pytest -q*"   # sugerencia prellenada
    assert pg.input_value("#ruleForm [name=kind]") == "bash"
    assert "*rm -rf*" in pg.locator(".rules").inner_text()            # reglas predeterminadas visibles
    pg.fill("#ruleForm [name=pattern]", "pytest*")
    pg.click("#ruleForm button[type=submit]")
    pg.wait_for_selector(".rule:has-text('pytest*')", timeout=4000)
    pg.click("#dlg button.row:has-text('Cerrar')")
    pg.wait_for_selector("text=⚖️ Regla agregada: Aprobar siempre · Comando «pytest*»", timeout=4000)
    ctx.close()


def test_cerrar_sesion_de_otro_dispositivo(browser, make_server):
    s = make_server("basic")
    pc_ctx, cel_ctx = new_context(browser, s), new_context(browser, s)   # cookies separadas
    pc, cel = pc_ctx.new_page(), cel_ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/?token={TOKEN}")
    cel.wait_for_selector("#input", timeout=5000)
    pc.wait_for_timeout(800)
    pc.click("#menuBtn")
    pc.click("[data-cmd=security]")
    pc.wait_for_selector(".sess")
    assert pc.locator(".sess").count() == 2 and pc.locator(".sess .me").count() == 1
    pc.on("dialog", lambda d: d.accept())
    pc.click("[data-revoke]")
    cel.wait_for_selector("input[name=token]", timeout=8000)            # el celular vuelve al login
    pc.wait_for_selector("#dlg .sess >> nth=0")
    assert pc.locator("#input").count() == 1                             # el PC sigue dentro
    pc_ctx.close(); cel_ctx.close()


def test_ver_auditoria(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pg = ctx.new_page()
    pg.goto(f"{s.base}/?token={TOKEN}")
    pg.wait_for_timeout(600)
    pg.fill("#input", "revisa ABC-123")
    pg.click("#send")
    pg.wait_for_selector(".card .yes", timeout=8000)
    pg.click(".card .yes")
    pg.wait_for_timeout(500)
    pg.click("#menuBtn")
    pg.click("[data-cmd=security]")
    pg.click("#openAudit")
    pg.wait_for_selector(".audit-row")
    text = pg.locator(".audit").inner_text()
    assert "🔓 Inicio de sesión (enlace/QR)" in text and "📝 Tarea: revisa ABC-123" in text
    assert "🛂 ✅ Aprobado · Bash pytest -q" in text
    pg.fill("#auditForm [name=q]", "Tarea")
    pg.press("#auditForm [name=q]", "Enter")
    pg.wait_for_selector("text=que contienen «Tarea»")
    assert pg.locator(".audit-row").count() == 1
    ctx.close()


def test_borrar_historial_en_todos_los_dispositivos(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    pc.wait_for_timeout(600)
    pc.fill("#input", "revisa ABC-123")
    pc.click("#send")
    cel.wait_for_selector(".card .yes", timeout=8000)
    pc.on("dialog", lambda d: d.accept())
    pc.click("#menuBtn")
    pc.click("[data-cmd=security]")
    pc.click("#clearHistory")
    cel.wait_for_selector("#hint", timeout=8000)                  # el otro dispositivo queda vacío
    assert cel.locator(".card").count() == 0
    ctx.close()


def test_activar_2fa_y_entrar_con_codigo(browser, make_server):
    from test_totp import code_at
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.click("#menuBtn")
    page.click("[data-cmd=security]")
    page.click("#totpBtn")
    page.wait_for_selector("#totpOut .newtoken")
    secret = page.inner_text("#totpOut .newtoken").replace(" ", "")
    assert page.get_attribute("#totpOut a", "href").startswith("otpauth://totp/")
    page.fill("#totpCode", "000000" if code_at(secret, -1) != "000000" else "111111")
    page.click("#totpForm button")
    page.wait_for_selector("#totpErr:has-text('incorrecto')")
    page.fill("#totpCode", code_at(secret, -1))
    page.click("#totpForm button")
    page.wait_for_selector("#toast:has-text('2FA activado')")
    ctx.close()

    ctx2 = new_context(browser, s)                     # otro dispositivo: pide el código
    p2 = ctx2.new_page()
    p2.goto(f"{s.base}/?token={TOKEN}")
    p2.wait_for_selector("input[name=code]")
    assert p2.locator("input[name=token]").count() == 0   # el token ya vino en el enlace
    p2.fill("input[name=code]", code_at(secret))
    p2.click("button")
    p2.wait_for_selector("#input", timeout=8000)
    ctx2.close()


@pytest.mark.parametrize("menu", ["security", "rules", "alerts"])
def test_dialogos_se_pueden_cerrar_en_pantalla_baja(browser, make_server, menu):
    """Como un iPhone con la barra de Safari: poco alto visible y diálogos largos."""
    s = make_server("basic")
    ctx = new_context(browser, s, viewport={"width": 375, "height": 480}, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")

    def abrir():
        page.click("#menuBtn")
        page.click(f"[data-cmd={menu}]")
        page.wait_for_selector("#dlg[open]")

    abrir()
    for sel in ("#dlgClose", "#dlgX"):                       # visibles sin hacer scroll
        box = page.locator(sel).bounding_box()
        assert box and box["y"] >= 0 and box["y"] + box["height"] <= 480, (sel, box)
    page.evaluate("dlg.scrollTop = dlg.scrollHeight")        # al final del contenido siguen a la vista
    box = page.locator("#dlgClose").bounding_box()
    assert box["y"] + box["height"] <= 480
    page.click("#dlgClose")
    assert page.locator("#dlg[open]").count() == 0

    abrir()
    page.click("#dlgX")
    assert page.locator("#dlg[open]").count() == 0

    abrir()
    page.mouse.click(2, 2)                                   # tocar el fondo oscuro
    assert page.locator("#dlg[open]").count() == 0
    ctx.close()


def test_dictado_con_whisper(browser, make_server):
    s = make_server("basic", FAKE_WHISPER="1")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#mic:not([hidden])")
    page.fill("#input", "revisa")
    page.click("#mic")
    page.wait_for_selector("#mic.rec")
    assert "Grabando" in page.get_attribute("#input", "placeholder")
    page.wait_for_timeout(1500)
    page.click("#mic")                                      # terminar
    page.wait_for_function("document.querySelector('#input').value.includes('texto dictado')", timeout=10000)
    value = page.input_value("#input")
    assert value.startswith("revisa texto dictado .") and "bytes" in value   # se agrega, no reemplaza
    assert page.locator("#mic.rec").count() == 0
    ctx.close()


def test_voz_sin_whisper_avisa_que_sale_a_internet(browser, make_server):
    s = make_server("basic", FAKE_WHISPER="0")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#mic:not([hidden])")
    assert "internet" in page.get_attribute("#mic", "title")
    page.click("#menuBtn")
    page.click("[data-cmd=voice]")
    page.wait_for_selector("#dlg[open] [data-voice=whisper]")
    assert "no está instalado" in page.inner_text("#dlgBody")
    page.click("[data-voice=whisper]")                      # solo Whisper: el botón ya no ofrece el navegador
    page.click("#dlgClose")
    page.click("#mic")
    page.wait_for_selector("#dlg[open]")                    # explica en vez de grabar
    assert page.locator("#mic.rec").count() == 0
    ctx.close()


def test_voz_desactivada_oculta_el_boton(browser, make_server):
    s = make_server("basic", VOICE_ENGINE="off")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    page.wait_for_timeout(800)
    assert page.locator("#mic").is_hidden()
    ctx.close()


FAKE_SYNTH = """(()=>{window.__spoken=[];window.__cancels=0;
const voices=[{name:'Paulina',lang:'es-MX',localService:true,voiceURI:'paulina'},
  {name:'Google español',lang:'es-ES',localService:false,voiceURI:'google-es'},
  {name:'Daniel',lang:'en-GB',localService:true,voiceURI:'daniel'}];
const fake={speaking:false,pending:false,getVoices:()=>voices,addEventListener(){},
  speak(u){window.__spoken.push({text:u.text,voice:u.voice&&u.voice.voiceURI})},cancel(){window.__cancels++}};
Object.defineProperty(window,'speechSynthesis',{value:fake,configurable:true});
window.SpeechSynthesisUtterance=class{constructor(t){this.text=t}};})()"""


def spoken(page):
    return [x["text"] for x in page.evaluate("window.__spoken")]


def test_respuestas_habladas_solo_con_el_interruptor(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    ctx.add_init_script(FAKE_SYNTH)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#ttsBtn:not([hidden])")
    assert page.inner_text("#ttsBtn") == "🔇"                  # apagado por defecto

    page.fill("#input", "revisa ABC-123")                       # con el interruptor apagado no habla
    page.click("#send")
    page.wait_for_selector(".card .yes")
    assert spoken(page) == []
    page.click(".card .yes")
    page.wait_for_selector(".card .opt", timeout=8000)
    page.click(".card .opt >> nth=0")
    page.wait_for_selector(".done", timeout=8000)

    page.click("#ttsBtn")                                       # encender
    assert page.inner_text("#ttsBtn") == "🔊"
    assert spoken(page) == ["Respuestas habladas activadas."]
    page.fill("#input", "otra vez")
    page.click("#send")
    page.wait_for_selector(".card:not(.closed) .yes", timeout=8000)
    said = spoken(page)
    assert "Voy a revisar ABC-123. (bloque de código)." in said      # sin Markdown ni el código
    assert "Claude necesita tu aprobación." in said
    voices = {x["voice"] for x in page.evaluate("window.__spoken")}
    assert voices == {"paulina"}                                # nunca la voz remota de Google

    page.reload()                                               # al recargar no relee el historial
    page.wait_for_selector("#ttsBtn.on")
    page.wait_for_timeout(800)
    assert spoken(page) == []

    page.click("#ttsBtn")                                       # apagar corta lo que esté leyendo
    assert page.inner_text("#ttsBtn") == "🔇"
    assert page.evaluate("window.__cancels") >= 1
    ctx.close()


def test_ajustes_de_respuestas_habladas(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    ctx.add_init_script(FAKE_SYNTH)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.click("#menuBtn")
    page.click("[data-cmd=voice]")
    page.wait_for_selector("#ttsVoiceSel")
    options = page.locator("#ttsVoiceSel option").all_inner_texts()
    assert options == ["Paulina (es-MX)"]                       # solo voces locales en español
    page.check("#ttsOn")
    assert page.inner_text("#ttsBtn") == "🔊"
    page.click("#ttsTest")
    assert spoken(page)[-1].startswith("Hola, así suenan")
    ctx.close()


def test_plantillas_desde_el_celular(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    cel.wait_for_selector("#tpls:not([hidden]) [data-tpl]")

    # plantilla con campo: se inserta y queda seleccionado {ticket}
    cel.click("#tpls button:has-text('Revisar ticket')")
    value = cel.input_value("#input")
    assert value.startswith("Revisa el ticket {ticket}")
    sel = cel.evaluate("[input.selectionStart, input.selectionEnd]")
    assert value[sel[0]:sel[1]] == "{ticket}"
    assert cel.locator("#tpls").is_hidden()                 # con texto en el cuadro se ocultan
    cel.keyboard.type("ABC-123")
    assert cel.input_value("#input").startswith("Revisa el ticket ABC-123 en Jira")
    cel.fill("#input", "")
    cel.wait_for_selector("#tpls:not([hidden])")

    # plantilla nueva desde el PC: aparece sola en el celular
    pc.click("#menuBtn")
    pc.click("[data-cmd=templates]")
    pc.click("#tplNew")
    pc.fill("#tplName", "🔎 Buscar TODO")
    pc.fill("#tplText", "Busca los TODO del proyecto y agrúpalos por archivo.")
    pc.check("#tplSend")
    pc.click("#tplSave")
    cel.wait_for_selector("#tpls button:has-text('Buscar TODO')", timeout=8000)

    # plantilla directa: se envía al tocarla
    cel.click("#tpls button:has-text('Buscar TODO')")
    pc.wait_for_selector(".msg.user:has-text('Busca los TODO del proyecto')", timeout=8000)
    ctx.close()


def test_autocompletar_comandos(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    page.click("#input")
    page.keyboard.type("/")
    page.wait_for_selector("#cmdList [data-cmd-name]", timeout=10000)
    assert page.locator("#cmdList [data-cmd-name]").count() == 5
    assert page.locator("#tpls").is_hidden()

    page.keyboard.type("co")                                   # filtra: primero los que empiezan así
    names = [b.get_attribute("data-cmd-name") for b in page.locator("#cmdList [data-cmd-name]").all()]
    assert names == ["compact", "context", "cost"]
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")                               # completa, no envía
    assert page.input_value("#input") == "/context "
    assert page.locator("#cmdList").is_hidden()
    assert page.locator(".msg.user").count() == 0

    page.fill("#input", "")
    page.keyboard.type("/jira")                                # también busca dentro del nombre
    page.wait_for_selector("#cmdList [data-cmd-name='atlassian:buscar-jira']")
    page.click("#cmdList [data-cmd-name='atlassian:buscar-jira']")
    assert page.input_value("#input") == "/atlassian:buscar-jira "

    page.fill("#input", "")
    page.keyboard.type("/zzz")
    page.wait_for_selector("#cmdList .cmd-note:has-text('Ningún comando')")
    page.keyboard.press("Escape")
    assert page.locator("#cmdList").is_hidden()
    ctx.close()


def test_enlaces_a_jira(browser, make_server):
    s = make_server("basic")
    s.login()
    s.api("/api/cmd", {"cmd": "jira_set", "url": "https://acme.atlassian.net", "projects": ""})
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.fill("#input", "revisa ABC-123 con UTF-8 y SHA-256")
    page.click("#send")
    page.wait_for_selector(".msg:not(.user) a.jira")             # «Voy a revisar **ABC-123**»
    links = page.locator(".msg.user a.jira")
    assert links.all_inner_texts() == ["ABC-123"]                # UTF-8 y SHA-256 no se enlazan
    a = links.first
    assert a.get_attribute("href") == "https://acme.atlassian.net/browse/ABC-123"
    assert a.get_attribute("target") == "_blank" and a.get_attribute("rel") == "noopener noreferrer"

    # bloques de código y enlaces existentes no se tocan; `código` solo si es exactamente una clave
    got = page.evaluate("""() => {const d=document.createElement('div');
        d.innerHTML=md('`ABC-1` y `ver ABC-2` y\\n```\\nABC-3\\n```\\nfin ABC-4')+'<a href="#">ABC-5</a>';
        linkify(d); return [...d.querySelectorAll('a.jira')].map(a=>a.textContent)}""")
    assert got == ["ABC-1", "ABC-4"]

    # limitar a otro proyecto: los enlaces de ABC desaparecen en todos los dispositivos
    s.api("/api/cmd", {"cmd": "jira_set", "url": "https://acme.atlassian.net", "projects": "DEV"})
    page.wait_for_function("document.querySelectorAll('a.jira').length === 0", timeout=8000)
    assert "ABC-123" in page.inner_text(".msg.user")
    ctx.close()


def test_configurar_jira_desde_la_interfaz(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.click("#menuBtn")
    page.click("[data-cmd=jira]")
    page.fill("#jiraUrl", "https://acme.atlassian.net")
    page.fill("#jiraProj", "abc")
    page.click("#jiraForm button[type=submit]")
    page.wait_for_selector("#toast:has-text('Enlaces a Jira activos')")
    page.fill("#input", "mira ABC-9 y OTRO-1")
    page.click("#send")
    page.wait_for_selector(".msg.user a.jira")
    assert page.locator(".msg.user a.jira").all_inner_texts() == ["ABC-9"]
    ctx.close()


def test_adjuntar_captura(browser, make_server, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    big = tmp_path / "captura.png"
    Image.new("RGB", (3000, 1500), (240, 240, 240)).save(big)            # se reduce a 2000 px en el navegador
    other = tmp_path / "foto.jpg"
    Image.new("RGB", (400, 300), (20, 90, 160)).save(other, "JPEG")
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    cel.wait_for_selector("#attachBtn")
    cel.set_input_files("#fileIn", [str(big), str(other)])
    cel.wait_for_selector("#attachBar .att >> nth=1")
    assert "2/5" in cel.inner_text("#attachBar")
    cel.click("#attachBar .att:nth-child(2) button")                   # quitar la segunda
    assert cel.locator("#attachBar .att").count() == 1
    sizes = cel.evaluate("attached.map(a=>a.media_type)")
    assert sizes == ["image/png"]
    dims = cel.evaluate("""(async()=>{const b=await (await fetch('data:image/png;base64,'+attached[0].data)).blob();
        const i=await createImageBitmap(b);return [i.width,i.height]})()""")
    assert dims == [2000, 1000]                                         # reducida antes de subir
    cel.fill("#input", "¿qué ves?")
    cel.click("#send")
    cel.wait_for_selector(".msg:has-text('Recibí 1 imagen(es): image/png')", timeout=8000)
    assert cel.locator("#attachBar").is_hidden()
    # la miniatura aparece en el mensaje, también en el otro dispositivo
    pc.wait_for_selector(".msg.user .thumbs img", timeout=8000)
    w = pc.evaluate("document.querySelector('.msg.user .thumbs img').naturalWidth")
    assert 0 < w <= 200
    ctx.close()


def test_sesiones_en_paralelo(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    pc.wait_for_selector("#tabs .tab.on:has-text('Principal')")

    # nueva pestaña desde el PC: queda abierta allí, pero no en el celular
    pc.click("#tabs [data-tab-add]")
    pc.fill("#tabName", "Front")
    pc.click("#tabForm button[type=submit]")
    pc.wait_for_selector("#tabs .tab.on:has-text('Front')")
    cel.wait_for_selector("#tabs .tab:has-text('Front')")
    assert "Principal" in cel.inner_text("#tabs .tab.on")

    pc.fill("#input", "tarea en Front")
    pc.click("#send")
    pc.wait_for_selector("#log .card .yes")                     # la tarjeta está en Front
    cel.wait_for_selector("#tabs .tab:has-text('Front') .tmark.wait")   # el celular ve que Front espera
    assert cel.locator("#log .card").count() == 0                # pero su pestaña abierta es otra
    assert cel.locator("#log .msg.user").count() == 0

    # en paralelo, una tarea en Principal desde el celular
    cel.fill("#input", "tarea en Principal")
    cel.click("#send")
    cel.wait_for_selector("#log .card .yes")
    pc.wait_for_selector("#tabs .tab:has-text('Principal') .tmark.wait")
    assert pc.locator("#log .msg.user").all_inner_texts() == ["tarea en Front"]
    assert pc.locator("main:visible").count() == 1 and cel.locator("main:visible").count() == 1   # un solo chat a la vista

    # el celular abre Front y aprueba; Principal sigue esperando
    cel.click("#tabs .tab:has-text('Front')")
    cel.wait_for_selector("#log .msg.user:has-text('tarea en Front')")
    cel.click("#log .card:not(.closed) .yes")
    pc.wait_for_selector("#log .msg:has-text('PermissionResultAllow')", timeout=8000)
    assert pc.locator("#tabs .tab:has-text('Principal') .tmark.wait").count() == 1

    # renombrar y cerrar desde el menú de la pestaña abierta
    pc.click("#tabs .tab.on")
    pc.fill("#tabNewName", "Frontend")
    pc.click("#tabRename button[type=submit]")
    cel.wait_for_selector("#tabs .tab:has-text('Frontend')")
    pc.on("dialog", lambda d: d.accept())
    pc.click("#tabs .tab.on")
    pc.click("#tabClose")
    pc.wait_for_selector("#tabs .tab.on:has-text('Principal')")
    cel.wait_for_function("document.querySelectorAll('#tabs .tab[data-tab]').length === 1", timeout=8000)
    assert cel.locator("#log .msg.user").all_inner_texts() == ["tarea en Principal"]
    ctx.close()


def test_cola_de_mensajes(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    pc.wait_for_selector("#input")
    cel.goto(f"{s.base}/")                                                   # ya con la cookie de sesión
    cel.wait_for_selector("#input")
    pc.fill("#input", "primera")
    pc.click("#send")
    pc.wait_for_selector(".card .yes")
    assert pc.is_enabled("#send") and "cola" in pc.get_attribute("#input", "placeholder")
    pc.fill("#input", "segunda")
    pc.click("#send")
    pc.type("#input", "tercera")                                             # se escribe mientras «segunda» se envía
    pc.click("#send")
    cel.wait_for_selector(".msg.queued:has-text('tercera')")              # se ve en el otro dispositivo
    pc.wait_for_function("document.querySelector('#wkMeta').textContent.includes('2 en cola')", timeout=5000)
    cel.click(".msg.queued:has-text('tercera') .qx")                         # quitar desde el celular
    pc.wait_for_selector(".msg.queued.dropped:has-text('tercera')")
    pc.click(".card:not(.closed) .yes")
    pc.wait_for_selector(".card:not(.closed) .opt", timeout=8000)
    pc.click(".card:not(.closed) .opt >> nth=0")
    # termina «primera» y «segunda» empieza sola: pasa de la cola a un mensaje normal
    pc.wait_for_selector(".msg.user:not(.queued):has-text('segunda')", timeout=8000)
    assert pc.locator(".msg.queued:has-text('segunda')").count() == 0
    assert pc.locator(".msg.user:not(.queued):has-text('tercera')").count() == 0
    ctx.close()


def test_elegir_modelo(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    cel.goto(f"{s.base}/")
    pc.wait_for_selector("#modelBtn:has-text('Predeterminado')")
    pc.click("#modelBtn")
    pc.click("[data-model=opus]")
    pc.wait_for_selector("#modelBtn.custom:has-text('Opus')")
    cel.wait_for_selector("#modelBtn:has-text('Opus')")                     # en todos los dispositivos
    ctx.close()


def test_sin_eleccion_si_hay_un_solo_modelo(browser, make_server):
    s = make_server("basic", CLAUDE_MODELS="default")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#modeBtn")
    page.wait_for_timeout(500)
    assert page.locator("#modelBtn").is_hidden()
    ctx.close()


def test_dialogo_de_costo_y_uso(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.fill("#input", "x")
    page.click("#send")
    page.click(".card .yes")
    page.wait_for_selector(".card:not(.closed) .opt", timeout=8000)
    page.click(".card:not(.closed) .opt >> nth=0")
    page.wait_for_selector(".done:has-text('1.5k tokens')", timeout=8000)
    page.click("#menuBtn")
    page.click("[data-cmd=usage]")
    page.wait_for_selector(".use-cards")
    assert "US$0.0012" in page.inner_text(".use-card >> nth=0")
    assert page.locator(".use-chart .bar").count() == 14
    assert "Principal" in page.inner_text(".use-table >> nth=0")
    ctx.close()


def test_barra_de_version_nueva(browser, make_server, tmp_path):
    import json as _json
    f = tmp_path / "release.json"
    f.write_text(_json.dumps({"tag_name": "v9.0.0", "html_url": "https://github.com/x/y/releases/tag/v9.0.0"}))
    s = make_server("basic", UPDATE_URL=f.as_uri(), UPDATE_FIRST_DELAY="0")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#updateBar:not([hidden])", timeout=10000)
    assert "v9.0.0" in page.inner_text("#updateText") and f"(tienes {__version__})" in page.inner_text("#updateText")
    assert page.get_attribute("#updateLink", "href").endswith("/v9.0.0")
    page.click("#updateLater")                                      # no volver a avisar de esta versión
    assert page.locator("#updateBar").is_hidden()
    page.reload()
    page.wait_for_selector("#input")
    page.wait_for_timeout(800)
    assert page.locator("#updateBar").is_hidden()
    ctx.close()


def test_respaldo_descargar_e_importar(browser, make_server, tmp_path):
    s = make_server("basic")
    ctx = new_context(browser, s, accept_downloads=True)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.click("#menuBtn")
    page.click("[data-cmd=backup]")
    with page.expect_download() as dl:
        page.click("#bkExport")
    path = tmp_path / "respaldo.json"
    dl.value.save_as(path)
    import json as _json
    data = _json.loads(path.read_text())
    assert data["app"] == "mando" and "device_prefs" in data and data["device_prefs"]["sound"] is True
    data["templates"].append({"name": "Desde el respaldo", "text": "hola", "send": True})
    data["device_prefs"]["tplBar"] = False
    path.write_text(_json.dumps(data))
    page.set_input_files("#bkFile", str(path))
    page.wait_for_selector("[data-bk=templates]")
    assert "(6)" in page.inner_text("label[for=bk_templates]")
    page.click("#bkImport")
    page.wait_for_selector("#toast:has-text('Importado')")
    assert page.evaluate("prefs.tplBar") is False                      # preferencias de este dispositivo
    s.login()
    names = [t["name"] for t in s.api("/api/cmd", {"cmd": "templates"})[1]["templates"]]
    assert "Desde el respaldo" in names
    ctx.close()


def test_buscar_en_el_historial(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    for t in ["Revisa la Acción del deploy", "algo distinto", "otra accion pendiente 🚀"]:
        page.fill("#input", t)
        page.click("#send")
        page.wait_for_selector(f".msg.user:has-text('{t[:10]}')")
    page.click("#findBtn")
    page.fill("#findInput", "ACCIÓN")                                     # sin importar mayúsculas ni tildes
    page.wait_for_function("document.querySelector('#findCount').textContent==='2/2'")
    assert page.locator("mark.hit").all_inner_texts() == ["Acción", "accion"]
    assert page.inner_text("mark.hit.cur") == "accion"                    # parte por lo más reciente
    page.press("#findInput", "Enter")
    page.wait_for_function("document.querySelector('#findCount').textContent==='1/2'")
    assert page.inner_text("mark.hit.cur") == "Acción"
    page.press("#findInput", "Shift+Enter")
    assert page.inner_text("#findCount") == "2/2"
    assert "🚀" in page.inner_text(".msg.user:has-text('otra')")          # el texto queda intacto
    page.fill("#findInput", "zzz")
    page.wait_for_function("document.querySelector('#findCount').textContent==='0'")
    page.fill("#findInput", "deploy")
    page.wait_for_function("document.querySelector('#findCount').textContent==='1/1'")
    page.press("#findInput", "Escape")
    assert page.locator("#findBar").is_hidden() and page.locator("mark.hit").count() == 0
    assert page.inner_text(".msg.user >> nth=0") == "Revisa la Acción del deploy"
    page.keyboard.press("Control+f")                                       # atajo de teclado
    assert page.locator("#findBar").is_visible()
    ctx.close()


def test_exportar_conversacion(browser, make_server, tmp_path):
    s = make_server("basic")
    ctx = new_context(browser, s, accept_downloads=True)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.fill("#input", "revisa ABC-123")
    page.click("#send")
    page.wait_for_selector(".card .yes")
    page.click("#menuBtn")
    page.click("[data-cmd=export]")
    with page.expect_download() as dl:
        page.click("#exDownload")
    assert dl.value.suggested_filename.startswith("claude-principal-")
    path = tmp_path / "c.md"
    dl.value.save_as(path)
    md = path.read_text()
    assert "## 🧑 Tú" in md and "revisa ABC-123" in md and "⏳ Sin decidir" in md
    ctx.close()


def test_limite_de_gasto(browser, make_server, tmp_path):
    import json as _json
    import time as _time
    home = tmp_path / "casa"
    home.mkdir()
    (home / ".mando-usage.jsonl").write_text(_json.dumps({"ts": _time.time() - 5, "cost": 8.5}) + "\n")
    s = make_server("basic", HOME=str(home), STATE_FILE=str(home / "state.json"), CLAUDE_WORKDIR=str(home),
                    ALLOWED_ROOTS=str(home), SPEND_LIMIT_USD="10")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#budgetBar:has-text('85 %')")                 # aviso: se acerca
    page.click("#budgetLater")
    assert page.locator("#budgetBar").is_hidden()
    # bajar el límite desde el diálogo de uso: queda bloqueado
    page.click("#menuBtn")
    page.click("[data-cmd=usage]")
    page.fill("#bgLimit", "8")
    page.click("#bgSave")
    page.wait_for_selector("#budgetBar.over:has-text('pausadas')")
    assert page.locator("#budgetLater").is_hidden()                       # el bloqueo no se puede ocultar
    page.wait_for_selector("#bgOverride")
    page.click("#dlgClose")
    page.fill("#input", "nueva tarea")
    page.click("#send")
    page.wait_for_selector("#toast:has-text('límite de gasto mensual')")
    assert page.input_value("#input") == "nueva tarea"                    # no se pierde lo escrito
    page.click("#budgetOverride")
    page.wait_for_selector("#budgetBar:has-text('continúas este mes')")
    page.click("#send")
    page.wait_for_selector(".msg.user:has-text('nueva tarea')")
    ctx.close()


def test_resumen_de_cambios(browser, make_server, tmp_path):
    from test_cambios import project
    w = project(tmp_path)
    s = make_server("cambios", CLAUDE_WORKDIR=str(w), ALLOWED_ROOTS=str(w))
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.fill("#input", "arregla f")
    page.click("#send")
    for i in range(1, 4):                                                   # Edit, Write y el comando
        page.click(".card:not(.closed) .yes")
        page.wait_for_function(f"document.querySelectorAll('.card.closed').length>={i}")
    page.wait_for_selector(".card.changes")
    assert "5 archivos" in page.inner_text(".changes .ch-sum")
    app = page.locator(".changes details:has-text('app.py')")
    assert app.locator(".diff").count() == 0                              # el diff se arma al abrir
    app.locator("summary").click()
    page.wait_for_selector(".changes details[open] .dl.add:has-text('return 2')")
    assert "binario" in page.inner_text(".changes details:has-text('logo.png')")
    page.click(".changes .ch-actions button")
    assert page.locator(".changes details[open]").count() == 5
    ctx.close()


def test_seccion_web_push(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.click("#menuBtn")
    page.click("[data-cmd=alerts]")
    page.wait_for_selector("#pushBox h4")
    text = page.inner_text("#pushBox")
    assert "Avisos con la app cerrada" in text
    if page.evaluate("'PushManager' in window"):
        page.wait_for_selector("#pushToggle:has-text('Activar en este dispositivo')")
        assert "cifrado" in text or "cifrado" in page.inner_text("#pushBox")
    ctx.close()


def test_aviso_de_decision_en_otra_pestana(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    pc, cel = ctx.new_page(), ctx.new_page()
    pc.goto(f"{s.base}/?token={TOKEN}")
    pc.wait_for_selector("#input")
    cel.goto(f"{s.base}/")
    cel.wait_for_selector("#tabs .tab.on:has-text('Principal')")
    assert cel.locator("#waitBar").is_hidden()
    pc.click("#tabs [data-tab-add]")
    pc.fill("#tabName", "Front")
    pc.click("#tabForm button[type=submit]")
    pc.wait_for_selector("#tabs .tab.on:has-text('Front')")
    pc.fill("#input", "tarea en Front")
    pc.click("#send")
    pc.wait_for_selector("#log .card .yes")
    # el celular sigue en «Principal»: la pestaña Front se marca y aparece la barra
    cel.wait_for_selector("#tabs .tab.waiting:has-text('Front')")
    cel.wait_for_selector("#waitBar:not([hidden]):not(.fading):has-text('«Front» espera tu decisión')")
    assert cel.locator("#tabs .tab.waiting").count() == 1 and "Principal" in cel.inner_text("#tabs .tab.on")
    assert pc.locator("#waitBar").is_hidden()                              # en el PC ya estás en esa pestaña
    cel.click("#waitBar")                                                  # te lleva a la decisión
    cel.wait_for_selector("#tabs .tab.on:has-text('Front')")
    cel.wait_for_selector("#log .card .yes")
    cel.wait_for_selector("#waitBar", state="hidden")                      # se va con un fundido
    cel.click("#log .card:not(.closed) .yes")
    cel.wait_for_selector("#log .card:not(.closed) .opt")                  # luego una pregunta: sigue esperando
    assert cel.locator("#tabs .tab.waiting:has-text('Front')").count() == 1
    cel.click("#log .card:not(.closed) .opt >> nth=0")
    pc.wait_for_function("!document.querySelector('#tabs .tab.waiting')")  # todo decidido: se apaga en todos lados
    ctx.close()


def test_respuesta_no_se_repite_al_terminar(browser, make_server):
    s = make_server("stream_temprano")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.fill("#input", "arregla el login")
    page.click("#send")
    page.wait_for_selector(".done")
    page.wait_for_timeout(300)
    assert page.locator(".msg:not(.user):has-text('corregí el login')").count() == 1
    assert page.locator(".msg.streaming").count() == 0
    page.reload()                                                          # y al recargar, tampoco
    page.wait_for_selector(".done")
    assert page.locator(".msg:not(.user):has-text('corregí el login')").count() == 1
    ctx.close()


def test_sugerir_regla(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    for i in range(2):
        page.fill("#input", f"corre las pruebas {i}")
        page.click("#send")
        page.click(".card:not(.closed) .yes")
        page.click(".card:not(.closed) .opt >> nth=0")
        page.wait_for_function(f"document.querySelectorAll('.done').length>={i + 1}")
    page.wait_for_selector(".rule-suggest:has-text('pytest -q*')")
    page.click(".rule-suggest .yes")
    page.wait_for_selector(".rule-suggest.closed:has-text('Regla creada')")
    page.fill("#input", "otra vez")
    page.click("#send")
    page.wait_for_selector(".info:has-text('Aprobado por regla')")
    ctx.close()


def test_adjuntar_archivo_de_texto(browser, make_server, tmp_path):
    s = make_server("eco")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    log = tmp_path / "app.log"
    log.write_text("ERROR timeout en la base de datos\n")
    env = tmp_path / ".env"
    env.write_text("TOKEN=secreto\n")
    page.set_input_files("#fileIn", [str(log), str(env)])
    page.wait_for_selector("#attachBar .att.file:has-text('app.log')")
    page.wait_for_selector("#toast:has-text('archivo de secretos')")
    assert page.locator("#attachBar .att.file").count() == 1                 # el .env no se adjunta
    page.fill("#input", "¿qué pasa?")
    page.click("#send")
    page.wait_for_selector(".msg.user .files:has-text('app.log')")
    page.wait_for_selector(".msg:not(.user):has-text('ERROR timeout en la base de datos')")   # llegó a Claude
    assert page.locator("#attachBar").is_hidden()
    page.reload()
    page.wait_for_selector(".msg.user .files:has-text('app.log')")
    assert "ERROR timeout" not in page.inner_text(".msg.user")              # en el historial, solo el nombre
    ctx.close()


def test_buscar_en_todas_las_pestanas(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    page.fill("#input", "deploy de la API en Principal")
    page.click("#send")
    page.wait_for_selector(".card .yes")
    page.click("#findBtn")
    assert page.locator("#findAll").is_hidden()                            # con una sola pestaña no aparece
    page.click("#findClose")
    page.click("#tabs [data-tab-add]")
    page.fill("#tabName", "Front")
    page.click("#tabForm button[type=submit]")
    page.wait_for_selector("#tabs .tab.on:has-text('Front')")
    page.fill("#input", "el deploy del front falla")
    page.click("#send")
    page.wait_for_selector("#log .msg.user:has-text('front falla')")
    page.click("#findBtn")
    page.fill("#findInput", "deploy")
    page.wait_for_function("document.querySelector('#findCount').textContent==='1/1'")   # solo esta pestaña
    page.click("#findAll")
    page.wait_for_function("document.querySelector('#findCount').textContent.startsWith('2/2 · Front')")
    assert page.inner_text("#tabs .tab:has-text('Principal') .tfind") == "1"
    page.press("#findInput", "Enter")                                      # salta a la otra pestaña
    page.wait_for_selector("#tabs .tab.on:has-text('Principal')")
    assert page.inner_text("#findCount") == "1/2 · Principal"
    assert page.locator("#log mark.hit.cur").count() == 1                  # visible en la pestaña abierta
    page.press("#findInput", "Escape")
    assert page.locator("mark.hit").count() == 0 and page.locator("#tabs .tfind").count() == 0
    page.click("#findBtn")                                                 # recuerda el modo «todas»
    assert page.get_attribute("#findAll", "aria-pressed") == "true"
    ctx.close()


def test_tareas_programadas(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    page.click("#menuBtn")
    page.click("[data-cmd=schedules]")
    page.click("#schNew")
    page.fill("#schName", "Pruebas de la mañana")
    page.fill("#schText", "corre las pruebas")
    page.click(".sch-days label:has-text('S')")                            # agrega el sábado a lun-vie
    page.fill("#schTime", "07:30")
    page.click("#schForm button[type=submit]")
    page.wait_for_selector("#schList .sched:has-text('Pruebas de la mañana')")
    assert "lun, mar, mié, jue, vie, sáb a las 07:30" in page.inner_text("#schList")
    assert "Próxima:" in page.inner_text("#schList")
    page.click("#schList .sched [data-a=run]")                             # ejecutar ahora
    page.wait_for_selector(".info:has-text('Tarea programada «Pruebas de la mañana»')")
    page.wait_for_selector(".msg.user:has-text('corre las pruebas')")
    page.click("#menuBtn")
    page.click("[data-cmd=schedules]")
    page.click("#schList .sched [data-a=toggle]")
    page.wait_for_selector("#schList .sched.off:has-text('Pausada')")
    page.click("#schList .sched [data-a=delete]")
    page.wait_for_selector("#schList:has-text('Aún no hay')")
    ctx.close()


def test_resumen_de_hoy(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.fill("#input", "revisa ABC-123")
    page.click("#send")
    page.click(".card:not(.closed) .yes")
    page.click(".card:not(.closed) .opt >> nth=0")
    page.wait_for_selector(".done")
    page.click("#menuBtn")
    page.click("[data-cmd=daily]")
    page.wait_for_selector(".daily-sum .ds-row")
    text = page.inner_text(".daily-sum")
    assert "1 tarea" in text and "1 aprobada" in text
    page.check("#dsOn")
    page.fill("#dsAt", "00:00")
    page.click("#dsSave")
    page.wait_for_selector("#toast:has-text('Resumen diario a las 00:00')")
    ctx.close()


def test_detener_junto_al_cuadro_de_texto(browser, make_server):
    s = make_server("detener_pregunta")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    assert page.locator("#stopBtn").is_hidden()                            # sin tarea, no aparece
    assert page.locator("header #stopBtn").count() == 0                    # ya no está arriba
    page.fill("#input", "algo largo")
    page.click("#send")
    page.wait_for_selector("footer #stopBtn:visible")
    page.click("#stopBtn")
    page.wait_for_selector(".info:has-text('Interrumpido')")
    page.wait_for_selector("#stopBtn", state="hidden")
    assert page.locator(".card:not(.closed)").count() == 0                 # la pregunta tardía no quedó abierta
    # Esc también detiene (con el cuadro vacío)
    page.fill("#input", "otra")
    page.click("#send")
    page.wait_for_selector("#stopBtn:visible")
    page.focus("#input")
    page.keyboard.press("Escape")
    page.wait_for_selector("#stopBtn", state="hidden", timeout=10000)
    ctx.close()


def test_tarjeta_huerfana_se_cierra(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    q = {"type": "question", "ref": "huerfana", "header": "Seguir", "question": "¿Sigo?",
         "options": [{"label": "Sí", "description": ""}], "multi": False}
    page.evaluate("q=>{q.tab=activeTab;render(q,true)}", q)                 # una tarjeta que el servidor no tiene
    page.wait_for_selector("#d-huerfana .opt")
    page.click("#d-huerfana .opt")                                         # responder: 404 → se cierra
    page.wait_for_selector("#d-huerfana.closed:has-text('Ya no está disponible')")
    assert page.evaluate("openCards.size") == 0
    # y si no se toca, se cierra sola al ver que el servidor no la tiene pendiente
    page.evaluate("q=>{q.tab=activeTab;q.ref='huerfana2';render(q,true);cardSeen['huerfana2']=0;reconcileCards()}", q)
    page.wait_for_selector("#d-huerfana2.closed:has-text('Ya no está pendiente')")
    assert page.locator("#tabs .tab.waiting").count() == 0
    ctx.close()


def test_maximo_de_pestanas(browser, make_server):
    s = make_server("basic", MAX_SESSIONS="2")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#tabs [data-tab-add]:not(.full)")
    page.click("#tabs [data-tab-add]")
    page.fill("#tabName", "Front")
    page.click("#tabForm button[type=submit]")
    page.wait_for_selector("#tabs [data-tab-add].full")                    # se ve deshabilitado
    page.locator("#tabs [data-tab-add]").dispatch_event("click")
    page.wait_for_selector("#toast:has-text('Ya tienes 2 pestañas, el máximo')")
    assert not page.locator("dialog[open]").count()
    ctx.close()


def test_pestana_con_codex(browser, make_server, tmp_path):
    import sys as _sys
    from conftest import ROOT
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    s = make_server("basic", CODEX_CMD=f"{_sys.executable} {ROOT / 'tests' / 'fake_codex.py'}")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#tabs [data-tab-add]")
    page.click("#tabs [data-tab-add]")
    page.fill("#tabName", "Back")
    page.select_option("#tabEngine", "codex")
    page.fill("#tabCwd", str(tmp_path))
    page.click("#tabForm button[type=submit]")
    page.wait_for_selector("#tabs .tab.on:has-text('Back') .tengine:has-text('Codex')")
    page.click("#modeBtn")
    assert page.locator("#dlgBody .mode-opt").count() == 3                 # los modos que entiende Codex
    page.click("#dlgClose")
    page.fill("#input", "/")
    assert page.locator("#cmdList").is_hidden()                            # sin comandos «/» con Codex
    page.fill("#input", "arregla f")
    page.click("#send")
    page.wait_for_selector(".msg:not(.user):has-text('Reviso el proyecto con Codex.')")
    page.wait_for_selector(".card:not(.closed):has-text('npm test')")
    page.click(".card:not(.closed) .yes")
    page.wait_for_selector(".card:not(.closed):has-text('app.py') .diff .dl.add:has-text('return 2')")
    page.click(".card:not(.closed) .yes")
    page.wait_for_selector(".msg:not(.user):has-text('comando=accept archivos=accept')")
    page.wait_for_selector(".done:has-text('tokens')")
    assert (tmp_path / "app.py").read_text().endswith("return 2\n")
    ctx.close()


def test_aprobacion_sigue_abierta_si_tardo_en_responder(browser, make_server):
    """Antes, a los 5 s se cerraba como «ya no está pendiente» con un estado viejo de la pestaña."""
    s = make_server("basic")
    ctx = new_context(browser, s)
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    page.fill("#input", "corre las pruebas")
    page.click("#send")
    page.wait_for_selector(".card .yes", timeout=8000)
    page.wait_for_timeout(7000)
    assert page.locator(".card:not(.closed) .yes").count() == 1      # sigue esperando la respuesta
    # un estado anterior a la tarjeta tampoco la cierra
    page.evaluate("""()=>{const r=[...openCards][0];cardSeen[r]=Date.now()-10000;statusAt=cardSeen[r]-1;
                    tabsInfo.forEach(t=>t.pending_refs=[]);reconcileCards()}""")
    assert page.locator(".card:not(.closed) .yes").count() == 1
    page.click(".card .yes")
    page.wait_for_selector(".card.closed:has-text('Aprobado')", timeout=5000)
    ctx.close()


def test_menu_agrupado_y_cursor_de_mano(browser, make_server):
    s = make_server("basic")
    ctx = new_context(browser, s, viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto(f"{s.base}/?token={TOKEN}")
    page.wait_for_selector("#input")
    page.click("#menuBtn")
    assert page.locator("#menu h4").all_text_contents() == ["Conversación", "Actividad", "Automatizar", "Preferencias"]
    assert page.get_attribute("#menuBtn", "aria-expanded") == "true"
    cursors = page.evaluate("[...document.querySelectorAll('#menuBtn,#findBtn,#send,#attachBtn,#tabs .tab,#menu button')]"
                            ".map(e=>getComputedStyle(e).cursor)")
    assert set(cursors) == {"pointer"}
    page.keyboard.press("Escape")                                      # se cierra con Esc
    assert not page.is_visible("#menu")
    page.click("#menuBtn")
    page.mouse.click(400, 500)                                         # y al tocar fuera
    assert not page.is_visible("#menu")
    page.click("#menuBtn")
    page.click("[data-cmd=usage]")                                     # una opción lo cierra y abre su diálogo
    page.wait_for_selector("dialog[open]")
    assert not page.is_visible("#menu")
    ctx.close()
