"""HTTPS con la CA local de gen-certs.sh."""

import shutil
import ssl
import subprocess
import urllib.error
import urllib.request

import pytest

from conftest import ROOT, TOKEN, has_openssl

pytestmark = pytest.mark.skipif(not has_openssl(), reason="requiere openssl")


@pytest.fixture
def certs(tmp_path):
    d = tmp_path / "tls"
    d.mkdir()
    shutil.copy(ROOT / "gen-certs.sh", d)
    out = subprocess.run(["bash", "gen-certs.sh", "192.168.1.50", "mi-pc.local"], cwd=d,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return d / "certs"


def test_certificado_incluye_ips_y_dominios(certs):
    san = subprocess.run(["openssl", "x509", "-in", certs / "server.crt", "-noout", "-ext", "subjectAltName"],
                         capture_output=True, text=True).stdout
    for name in ["DNS:localhost", "IP Address:127.0.0.1", "IP Address:192.168.1.50", "DNS:mi-pc.local"]:
        assert name in san
    assert oct((certs / "server.key").stat().st_mode)[-3:] == "600"


def test_https_valido_con_la_ca_y_rechazado_sin_ella(make_server, certs):
    s = make_server(TLS_CERT=str(certs / "server.crt"), TLS_KEY=str(certs / "server.key"),
                    CA_CERT=str(certs / "ca.crt"))
    s.ssl_ctx = ssl.create_default_context(cafile=str(certs / "ca.crt"))
    s.login()
    cookie = next(c for c in s.jar if c.name == "cr_session")
    assert cookie.secure, "la cookie debe ser Secure con HTTPS"

    status, h, body = s.request("GET", "/ca.crt", cookies=False)
    assert status == 200 and h["Content-Type"] == "application/x-x509-ca-cert"
    assert body == (certs / "ca.crt").read_bytes()

    with pytest.raises(urllib.error.URLError) as e:  # sin la CA, como un teléfono sin instalarla
        urllib.request.urlopen(f"{s.base}/?token={TOKEN}", timeout=5)
    assert isinstance(e.value.reason, ssl.SSLCertVerificationError)
