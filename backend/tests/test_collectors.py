"""Testes dos coletores do CP2 (não dependem de FastAPI)."""
import http.server
import threading

from app import collectors as c


def test_parse_cpu_line():
    total, idle = c.parse_cpu_line("cpu  100 0 50 800 50 0 0 0 0 0")
    assert total == 1000 and idle == 850


def test_cpu_sampler_percent():
    # 1ª leitura (base) e 2ª leitura: +200 de total, +100 ociosos -> 50 %
    readings = iter([(1000, 800), (1200, 900)])
    t = iter([0.0, 5.0])
    s = c.CpuSampler(reader=lambda: next(readings), clock=lambda: next(t))
    assert s.percent() == 50.0


def test_cpu_sampler_reuses_value_when_called_too_fast():
    readings = iter([(1000, 800), (1200, 900), (9999, 0)])
    t = iter([0.0, 5.0, 5.2])
    s = c.CpuSampler(reader=lambda: next(readings), clock=lambda: next(t))
    first = s.percent()
    assert s.percent() == first  # 0,2 s depois: não recalcula


def test_parse_meminfo():
    m = c.parse_meminfo("MemTotal: 2048000 kB\nMemFree: 100 kB\nMemAvailable: 1024000 kB\n")
    assert m == {"total_mb": 2000, "used_mb": 1000, "available_mb": 1000, "percent": 50.0}


def test_format_uptime():
    assert c.format_uptime(59) == "0min"
    assert c.format_uptime(3700) == "1h 1min"
    assert c.format_uptime(90061) == "1d 1h 1min"


def test_disk_usage_missing_path_is_marked_unavailable():
    assert c.disk_usage("/caminho/que/nao/existe") == {
        "path": "/caminho/que/nao/existe",
        "error": "indisponível",
    }


def test_classify_states():
    p = lambda a, load="loaded": {"LoadState": load, "ActiveState": a}  # noqa: E731
    assert c.classify(p("active")) == "ativo"
    assert c.classify(p("inactive")) == "inativo"
    assert c.classify(p("failed")) == "falha"
    assert c.classify(p("activating")) == "desconhecido"
    assert c.classify(p("inactive", load="not-found")) == "desconhecido"
    assert c.classify(None) == "desconhecido"  # systemctl indisponível


def test_parse_show():
    out = "LoadState=loaded\nActiveState=active\nSubState=running\n"
    assert c.parse_show(out) == {"LoadState": "loaded", "ActiveState": "active", "SubState": "running"}


def test_probe_port_refused_vs_unknown():
    # porta fechada em localhost -> recusada -> "fechada"
    assert c.probe_port("127.0.0.1", 1) == "fechada"
    # endereço inválido -> erro de rede -> "desconhecido" (e não "fechada")
    assert c.probe_port("host.invalido.test", 80, timeout=0.5) == "desconhecido"


def test_collect_metrics_shape():
    m = c.collect_metrics()
    assert 0 <= m["cpu_percent"] <= 100
    assert m["memory"]["total_mb"] > 0
    assert len(m["disks"]) == 2 and m["disks"][0]["path"] == "/"
    assert m["uptime_seconds"] > 0 and m["timestamp"]


def test_http_latency_ok_and_error_codes():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404 if self.path == "/x" else 200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        ok = c.http_latency(base + "/")
        assert ok["ok"] and ok["status_code"] == 200 and ok["duration_ms"] >= 0
        nf = c.http_latency(base + "/x")
        assert nf["ok"] and nf["status_code"] == 404  # respondeu: há latência
    finally:
        srv.shutdown()


def test_http_latency_unreachable_returns_error_not_fake_numbers():
    r = c.http_latency("http://127.0.0.1:1/", timeout=1)
    assert r["ok"] is False and r["duration_ms"] is None and r["status_code"] is None
