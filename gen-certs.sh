#!/usr/bin/env bash
# Genera una CA local y un certificado HTTPS para Mando.
#
# Las notificaciones del sistema exigen HTTPS con un certificado en el que el
# teléfono confíe. Con esta CA propia: la instalas una vez en el teléfono y
# el certificado del servidor queda como válido (sin advertencias).
#
# Uso:  ./gen-certs.sh [IP | nombre ...]
#       La IP local se detecta e incluye siempre. Ej.: ./gen-certs.sh 192.168.1.50 mi-pc.local
# Crea: certs/ca.crt  certs/ca.key  certs/server.crt  certs/server.key
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p certs && chmod 700 certs

# IP local (siempre) + lo que pases como argumento: otras IPs o nombres del equipo
LOCAL_IP=$(python3 - <<'EOF2'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("10.254.254.254", 1)); print(s.getsockname()[0])
except OSError:
    print("")
EOF2
)

SAN="DNS:localhost,IP:127.0.0.1"
for name in $LOCAL_IP "$@"; do
  [ -z "$name" ] && continue
  case ",$SAN," in *":$name,"*) continue;; esac          # evita duplicados
  if [[ "$name" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ || "$name" == *:* ]]; then
    SAN="$SAN,IP:$name"
  else
    SAN="$SAN,DNS:$name"
  fi
done

if [ ! -f certs/ca.key ]; then
  echo "→ Creando CA local (certs/ca.crt), válida 10 años"
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
    -keyout certs/ca.key -out certs/ca.crt \
    -subj "/CN=Mando CA local/O=Mando" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
else
  echo "→ Reutilizando CA existente (certs/ca.crt)"
fi

echo "→ Creando certificado del servidor para: $SAN"
openssl req -newkey rsa:2048 -nodes -sha256 \
  -keyout certs/server.key -out certs/server.csr -subj "/CN=mando" 2>/dev/null
cat > certs/server.ext <<EOF
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
EOF
# 825 días: máximo que aceptan iOS/macOS para certificados de servidor
openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key -CAcreateserial \
  -days 825 -sha256 -extfile certs/server.ext -out certs/server.crt 2>/dev/null
rm -f certs/server.csr certs/server.ext certs/ca.srl
chmod 600 certs/*.key

cat <<EOF

Listo. Agrega a tu .env:
  TLS_CERT=./certs/server.crt
  TLS_KEY=./certs/server.key
  CA_CERT=./certs/ca.crt

Luego instala la CA en el teléfono (una sola vez) desde:
  https://<IP-del-PC>:8765/ca.crt   (acepta la advertencia solo esa vez)
  → ver README, sección «Notificaciones en el teléfono»
Si cambia la IP local, vuelve a ejecutar este script (la CA se reutiliza
y no hay que reinstalarla en el teléfono).
EOF
