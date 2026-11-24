#!/usr/bin/env python3
"""Medição do laboratório virtual (seção 8.3.1, passo 10) pela API do console.

Para cada perfil pedido, repete N vezes: solicita um desktop, espera ficar
disponível (ou falhar), registra o tempo até a prontidão e as medidas do host
e da instância, desliga, exclui e espera a exclusão terminar. As rodadas são
SEQUENCIAIS (uma instância por vez), como permite o enunciado.

Uso (no Debian, com o console rodando):
    python3 scripts/lab_medicao.py --user admin1 --profiles p1 p2 --repeat 3 --out medicao-lab.csv
A senha é pedida sem eco. Só biblioteca padrão.
"""
import argparse
import csv
import getpass
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

H = {"X-Requested-With": "console", "Content-Type": "application/json"}


class Console:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.base + "/api" + path, method=method, headers=H,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=60) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read() or b"{}").get("detail")
            raise SystemExit(f"{method} {path}: HTTP {exc.code} {detail}") from exc


def wait_state(c: Console, iid: int, targets: set[str], timeout: int) -> dict:
    start = time.monotonic()
    while True:
        inst = c.call("GET", f"/lab/instances/{iid}")
        if inst["state"] in targets:
            return inst
        if time.monotonic() - start > timeout:
            raise SystemExit(f"instância #{iid} não chegou a {targets} em {timeout}s (estado {inst['state']})")
        time.sleep(3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.100.10:8000")
    ap.add_argument("--user", required=True)
    ap.add_argument("--image", default="debian12-xfce-v1")
    ap.add_argument("--profiles", nargs="+", default=["p1", "p2"])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--idle", type=int, default=30, help="segundos ociosos antes de medir a instância")
    ap.add_argument("--out", default="medicao-lab.csv")
    args = ap.parse_args()

    c = Console(args.url)
    c.call("POST", "/auth/login", {"username": args.user, "password": getpass.getpass("Senha do console: ")})
    fields = ["perfil", "rodada", "instancia", "vcpu", "mem_mib", "segundos_ate_pronta", "resultado",
              "host_cpu_pct", "host_ram_livre_mib", "host_disco_livre_gb", "vm_cpu_pct", "vm_ram_mib"]
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        out = csv.DictWriter(fh, fieldnames=fields)
        out.writeheader()
        for profile in args.profiles:
            for n in range(1, args.repeat + 1):
                r = c.call("POST", "/lab/instances",
                           {"image_id": args.image, "profile_id": profile, "op_id": secrets.token_hex(16)})
                iid = r["instance"]["id"]
                print(f"[{profile} #{n}] instância #{iid} solicitada", flush=True)
                inst = wait_state(c, iid, {"disponivel", "falha"}, timeout=900)
                row = {"perfil": profile, "rodada": n, "instancia": inst["name"], "vcpu": inst["cpus"],
                       "mem_mib": inst["memory_mb"], "segundos_ate_pronta": inst["seconds_to_ready"],
                       "resultado": inst["state"]}
                if inst["state"] == "disponivel":
                    time.sleep(args.idle)
                    inst = c.call("GET", f"/lab/instances/{iid}")
                    host = (c.call("GET", "/lab/capacity").get("host") or {}).get("host") or {}
                    row.update(host_cpu_pct=host.get("cpu_percent"), host_ram_livre_mib=host.get("ram_available_mb"),
                               host_disco_livre_gb=host.get("disk_free_gb"), vm_cpu_pct=inst.get("measured_cpu"),
                               vm_ram_mib=inst.get("measured_ram_mb"))
                    c.call("POST", f"/lab/instances/{iid}/stop")
                    wait_state(c, iid, {"desligada"}, timeout=180)
                out.writerow(row)
                fh.flush()
                print(f"[{profile} #{n}] {row['resultado']} em {row['segundos_ate_pronta']}s", flush=True)
                c.call("POST", f"/lab/instances/{iid}/delete")
                wait_state(c, iid, {"excluida"}, timeout=400)
    print(f"Resultados em {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
