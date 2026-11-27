"""Chequeos de sintaxis de los archivos que no son Python."""

import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

node = shutil.which("node")


@pytest.mark.skipif(not node, reason="requiere node")
def test_javascript_de_la_interfaz(tmp_path):
    scripts = re.findall(r"<script>([\s\S]*?)</script>", (ROOT / "ui.html").read_text())
    assert len(scripts) >= 2                     # el del tema (en <head>) y el principal
    for i, script in enumerate(scripts):
        f = tmp_path / f"ui{i}.js"
        f.write_text(script)
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not node, reason="requiere node")
def test_service_worker():
    r = subprocess.run([node, "--check", str(ROOT / "sw.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", ["gen-certs.sh", "vpn/tailscale-subnet-router.sh"])
def test_scripts_de_shell(script):
    r = subprocess.run(["bash", "-n", str(ROOT / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_archivos_para_publicar():
    assert "MIT License" in (ROOT / "LICENSE").read_text()
    assert "Report a vulnerability" in (ROOT / "SECURITY.md").read_text()


def test_manifiesto():
    m = json.loads((ROOT / "manifest.webmanifest").read_text())
    assert m["start_url"] == "/" and m["icons"][0]["src"] == "/icon.svg"


def test_env_example_sin_secretos():
    text = (ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(k in key for k in ("TOKEN", "KEY", "SECRET")):
            assert value.strip() == "", f"{key} no debe traer un valor en .env.example"
