# Acceso remoto con VPN (Tailscale + Raspberry Pi)

Esta es la forma recomendada de usar Mando fuera de casa, **sin abrir puertos en el router** y sin instalar nada en el PC corporativo.

¿Prefieres usar un computador en vez de la Raspberry Pi, o conectarte desde un notebook? Ver **[vpn-tailscale-pc-mac.md](vpn-tailscale-pc-mac.md)**.

```
Celular (datos móviles, app Tailscale)
      │  túnel cifrado (WireGuard)
      ▼
Raspberry Pi en casa  ──── «subnet router» de 192.168.1.0/24
      │
      ▼
PC con Mando  https://192.168.1.X:8765   (la misma dirección que usas en casa)
```

- El celular "entra" a tu red de casa. Usas la **misma URL privada** de siempre y el certificado y la CA que ya instalaste siguen sirviendo.
- La Raspberry Pi reenvía el tráfico con su propia IP (SNAT), así que Mando lo ve como una conexión de tu red local. No hace falta cambiar `ALLOWED_NETS`.
- Funciona aunque tu proveedor use CGNAT y con cualquier router, incluso los que solo traen cliente VPN (L2TP/PPTP) y no pueden actuar como servidor.
- Tailscale coordina las conexiones desde sus servidores, pero el tráfico va cifrado de extremo a extremo. El plan Personal es gratuito.

> Si antes abriste un puerto en el router para Mando, bórralo: con la VPN no hace falta.

> **Atajo:** el script [`vpn/tailscale-subnet-router.sh`](../vpn/tailscale-subnet-router.sh) hace los pasos 2 a 4 de una vez: detecta tu red, instala Tailscale, activa el reenvío y anuncia la ruta. Cópialo a la Pi y ejecútalo con `sudo bash tailscale-subnet-router.sh`. Después sigue en el paso 5.

## 1. Preparar la Raspberry Pi
- Usa Raspberry Pi OS (o Armbian/Debian en una Orange Pi). Conéctala por **cable** al router y déjala siempre encendida.
- Recomendado: en el router, reserva por DHCP una IP fija para la Pi y otra para el PC donde corre Mando, así la URL no cambia.
- Actualiza el sistema:
  ```bash
  sudo apt update && sudo apt full-upgrade -y
  ```

## 2. Instalar Tailscale
```bash
curl -fsSL https://tailscale.com/install.sh | sh
```
Queda como servicio y arranca solo al encender la Pi.

## 3. Activar el reenvío de IP
```bash
echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
```

## 4. Conectar la Pi y anunciar tu red
```bash
sudo tailscale up --advertise-routes=192.168.1.0/24
```
La terminal muestra un enlace: ábrelo e inicia sesión. Crea la cuenta con una cuenta **personal** (Google, GitHub, Apple o Microsoft), no con la de la empresa.

Usa la red de tu casa: en estos ejemplos es `192.168.1.0/24`, pero muchas usan `192.168.0.x` u otra. Ajusta la ruta. La ves con `ip -4 addr` en la Pi.

## 5. Aprobar la ruta en la consola
En <https://login.tailscale.com/admin/machines>:
1. Busca la Pi (aparece con la etiqueta **Subnets**) → menú **⋯** → **Edit route settings…**
2. Marca `192.168.1.0/24` y guarda.
3. En el mismo menú, elige **Disable key expiry**, para que la Pi no se desconecte sola cuando venza su llave.

## 6. Instalar Tailscale en el teléfono
- Instala la app **Tailscale** (Play Store o App Store) e inicia sesión con la **misma cuenta**.
- Activa la conexión. Android e iOS toman la ruta de tu red automáticamente.

## 7. Probar
1. En el teléfono, apaga el Wi-Fi y quédate con datos móviles.
2. Con Tailscale activo, abre `https://192.168.1.X:8765`, con la IP del PC.
3. Deberías ver Mando igual que en casa, con las alertas y notificaciones funcionando.

Si no carga:
- Revisa que la ruta esté aprobada (paso 5) y que la app del teléfono esté conectada.
- Desde la Pi, comprueba que llega al PC: `curl -k https://192.168.1.X:8765/icon.svg`.
- Revisa el firewall del PC: debe aceptar conexiones de la red local en el puerto 8765.

## Recomendaciones
- **Conflicto de redes:** si algún día estás en otra red Wi-Fi que también use `192.168.1.x`, la ruta choca. En ese caso usa datos móviles.
- **Acceso:** solo los dispositivos que inicien sesión en tu cuenta de Tailscale entran a tu red. Revisa de vez en cuando la lista de equipos en la consola y elimina los que no reconozcas.
- **Consumo:** una Raspberry Pi gasta unos pocos watts. Un PC antiguo también sirve, pero consume bastante más estando encendido todo el día.
- **Batería del teléfono:** puedes dejar Tailscale activo siempre o encenderlo solo cuando lo necesites.
