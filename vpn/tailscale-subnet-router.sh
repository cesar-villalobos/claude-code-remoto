#!/usr/bin/env bash
# Convierte una Raspberry Pi / Orange Pi / PC con Linux en "puente" Tailscale
# (subnet router) hacia tu red de casa. Así el celular, con la app de Tailscale,
# llega a Mando por su IP privada desde cualquier lugar:
#   - sin abrir puertos en el router (funciona aunque haya CGNAT)
#   - sin instalar nada en el PC donde corre Mando
#
# Uso (en la Pi):   sudo bash tailscale-subnet-router.sh [red/máscara]
#                   p. ej. sudo bash tailscale-subnet-router.sh 192.168.1.0/24
#                   (sin argumento detecta la red local automáticamente)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Ejecuta con sudo: sudo bash $0 [red/máscara]" >&2
  exit 1
fi

# 1) Red a publicar: argumento o la red de la interfaz con la ruta por defecto
SUBNET="${1:-}"
if [ -z "$SUBNET" ]; then
  IFACE=$(ip -4 route show default | awk '{print $5; exit}')
  SUBNET=$(ip -4 route show dev "$IFACE" proto kernel scope link | awk '{print $1; exit}')
fi
if ! [[ "$SUBNET" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]]; then
  echo "No pude detectar la red local. Indícala, p. ej.: sudo bash $0 192.168.1.0/24" >&2
  exit 1
fi
case "$SUBNET" in
  10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
  *) echo "La red $SUBNET no es privada; revisa el argumento." >&2; exit 1 ;;
esac
echo "→ Red de casa que se publicará por la VPN: $SUBNET"

# 2) Instalar Tailscale (script oficial) si no está
if ! command -v tailscale >/dev/null 2>&1; then
  echo "→ Instalando Tailscale…"
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "→ Tailscale ya está instalado ($(tailscale version | head -1))"
fi

# 3) Permitir que la Pi reenvíe tráfico entre la VPN y la red de casa
echo "→ Activando reenvío de IP"
cat > /etc/sysctl.d/99-tailscale.conf <<EOF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -p /etc/sysctl.d/99-tailscale.conf >/dev/null

# 4) Conectar y anunciar la ruta (la primera vez muestra un enlace para iniciar sesión)
echo "→ Conectando a Tailscale y anunciando $SUBNET"
echo "  Si aparece un enlace, ábrelo en cualquier navegador e inicia sesión con tu cuenta."
tailscale up --advertise-routes="$SUBNET" --accept-dns=false

cat <<EOF

✅ La Pi está en tu red Tailscale como puente hacia $SUBNET.

Falta (una sola vez) en https://login.tailscale.com/admin/machines :
  1. En esta máquina: ⋯ → «Edit route settings» → activa $SUBNET → Save.
  2. En esta máquina: ⋯ → «Disable key expiry» (para que no se desconecte a los 180 días).

Luego, en el celular: instala la app Tailscale, entra con la MISMA cuenta,
actívala y abre Mando con su IP privada de siempre.
EOF
