# Acceso remoto con VPN usando un PC o Mac (Tailscale)

Esta guía es la variante de [vpn-tailscale.md](vpn-tailscale.md) para **computadores**. Cubre dos usos:

| Uso | Para qué |
|---|---|
| **A. PC o Mac como puente** (*subnet router*) | Un computador de casa siempre encendido reemplaza a la Raspberry Pi y da acceso a toda tu red `192.168.1.x`. |
| **B. PC o Mac como cliente** | Un notebook que llevas fuera de casa y desde el que quieres abrir Claude Remoto. |

> No uses como puente el PC corporativo donde corre Claude Remoto. El puente debe ser **otro** equipo de casa, así el corporativo queda sin tocar.

Antes de empezar:
- Crea tu cuenta de Tailscale con una cuenta **personal** (Google, GitHub, Apple o Microsoft).
- En el router, reserva por DHCP una IP fija para el puente y otra para el PC de Claude Remoto.
- Averigua tu red (en estos ejemplos, `192.168.1.0/24`). Si tu router está en `192.168.1.1`, la red es `192.168.1.0/24`; si está en `192.168.0.1`, es `192.168.0.0/24`. Para confirmarlo, ejecuta `ipconfig` en Windows o `ip -4 addr` / `ifconfig` en Linux y Mac.

---

## A. PC o Mac como puente

Los pasos son los mismos en todos los sistemas:

1. Instalar Tailscale.
2. Activar el reenvío de IP.
3. Anunciar la red.
4. Aprobarla en la consola.
5. Evitar que el equipo se suspenda.

### Windows (10/11)
1. **Instalar:** descarga Tailscale desde <https://tailscale.com/download/windows>, instálalo e inicia sesión desde el ícono de la bandeja.
2. **Activar el reenvío de IP:** Windows no lo trae activado. En **PowerShell como administrador**:
   ```powershell
   Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters" -Name "IPEnableRouter" -Value 1
   ```
   **Reinicia** el equipo para que tome efecto.
3. **Anunciar la red:** en PowerShell (no necesita ser administrador):
   ```powershell
   tailscale set --advertise-routes=192.168.1.0/24
   ```
4. **Que funcione sin sesión iniciada:** en el ícono de Tailscale, ve a *Preferences* y activa **Run unattended**. Así sigue activo aunque cierres la sesión de Windows.
5. **Que no se suspenda:** ve a *Configuración → Sistema → Inicio/apagado y batería* (o *Energía*) y pon *Suspender* en **Nunca** cuando está enchufado.
6. **Firewall:** si Windows pregunta, permite Tailscale en redes privadas.

### macOS
1. **Instalar:** descarga Tailscale desde <https://tailscale.com/download/mac> o desde la App Store, e inicia sesión. Cualquier versión sirve como puente.
2. **Habilitar el comando `tailscale`** (si usas la versión de App Store o la standalone): en el menú de Tailscale, ve a *Settings* y elige **Install CLI**. Como alternativa, usa la ruta completa `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
3. **Anunciar la red:** en la Terminal:
   ```bash
   sudo tailscale set --advertise-routes=192.168.1.0/24
   ```
   En macOS el reenvío de IP se activa solo al anunciar rutas. Si no funciona, actívalo a mano con `sudo sysctl -w net.inet.ip.forwarding=1`.
4. **Que arranque solo:** activa *Launch at login* en la configuración de Tailscale, y en *Ajustes del Sistema → General → Ítems de inicio*.
5. **Que no se suspenda:** ve a *Ajustes del Sistema → Batería* (o *Energía*) y activa *Evitar la suspensión automática cuando la pantalla está apagada* y *Arrancar automáticamente tras un corte de energía*. También puedes hacerlo por Terminal:
   ```bash
   sudo pmset -c sleep 0 disksleep 0
   ```
   En un MacBook, déjalo enchufado. Si cierras la tapa sin una pantalla externa, se suspende igual.

### Linux (PC antiguo con Ubuntu, Debian, etc.)
Es lo mismo que en la Raspberry Pi:
```bash
curl -fsSL https://tailscale.com/install.sh | sh
echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
sudo tailscale up --advertise-routes=192.168.1.0/24
```
Para que no se suspenda:
```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```
Si es un notebook, también configura que no se suspenda al cerrar la tapa: en `/etc/systemd/logind.conf` pon `HandleLidSwitch=ignore` y ejecuta `sudo systemctl restart systemd-logind`.

### Aprobar la ruta (todos los sistemas)
En <https://login.tailscale.com/admin/machines>:
1. Busca el equipo puente (aparece con la etiqueta **Subnets**) → menú **⋯** → **Edit route settings…**
2. Marca `192.168.1.0/24` y guarda.
3. En el mismo menú, elige **Disable key expiry**. Si la llave del puente vence, las rutas que anuncia dejan de funcionar.

### Comprobar
- En el puente, `tailscale status` debe mostrar el equipo conectado.
- En el teléfono (con la app Tailscale y datos móviles), abre `https://192.168.1.X:8765`, con la IP del PC de Claude Remoto.

---

## B. PC o Mac como cliente (fuera de casa)

Sirve para abrir Claude Remoto desde un notebook personal fuera de casa:

| Sistema | Pasos |
|---|---|
| **Windows / macOS** | Instala Tailscale, inicia sesión con la **misma cuenta** y actívalo. Las rutas de tu red se toman solas. |
| **Linux** | Instálalo e inicia sesión. Además, ejecuta `sudo tailscale set --accept-routes`: en Linux las rutas no se aceptan por defecto. |

Luego abre `https://192.168.1.X:8765` en el navegador.

**Certificado:** para que el navegador no muestre advertencias, instala también en ese computador la CA local (`https://192.168.1.X:8765/ca.crt`):
- **Windows:** doble clic → *Instalar certificado* → *Equipo local* → *Colocar todos los certificados en el siguiente almacén* → **Entidades de certificación raíz de confianza**.
- **macOS:** doble clic para abrirlo en *Acceso a Llaveros*, en el llavero **Sistema**. Luego doble clic en «Claude Remoto CA local» → *Confiar* → **Confiar siempre**.
- **Linux (Chrome/Chromium):** ve a *Configuración → Privacidad y seguridad → Seguridad → Gestionar certificados → Autoridades* → *Importar*, y marca *Confiar en este certificado para identificar sitios web*.

> **No lo hagas en el PC corporativo.** Instalar Tailscale o una CA propia en un equipo de la empresa probablemente infringe sus políticas y puede chocar con la VPN corporativa. Usa un computador personal como cliente.

---

## Problemas comunes
| Síntoma | Solución |
|---|---|
| `--advertise-routes` da error de reenvío de IP | Windows: aplica el cambio de registro y **reinicia**. Linux: repite los comandos `sysctl`. macOS: `sudo sysctl -w net.inet.ip.forwarding=1` |
| La ruta no aparece en la consola | Comprueba que ejecutaste `tailscale set --advertise-routes=…` en el puente y que dice *Subnets* |
| Funciona un rato y luego no | El puente se suspendió, o venció su llave: desactiva la suspensión y activa *Disable key expiry* |
| Funciona en casa pero no fuera | Prueba con datos móviles. Si la red donde estás también usa `192.168.1.x`, las redes chocan |
| El navegador advierte del certificado | Instala la CA local en ese dispositivo (ver arriba) |
