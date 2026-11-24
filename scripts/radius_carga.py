#!/usr/bin/env python3
"""Carga controlada de autenticação RADIUS (experimento do serviço exclusivo, seção 8.5).

Roda no Ubuntu (cadastrado como cliente NAS no console). Envia Access-Requests
PAP com Message-Authenticator, em N "trabalhadores" paralelos de laço fechado
(cada um espera a resposta antes do próximo pedido), e mede vazão, latência
(média, p50, p95, p99), aceitos, negados e perdidos. Só biblioteca padrão.

Exemplo:
    python3 radius_carga.py --server 192.168.100.10 --secret-file ~/nas.secret \\
        --password 'senha-dos-usuarios-de-carga' --users 5000 --requests 1000 --parallel 1 10 50 \\
        --label "ssha2 5000 usuarios" --csv resultados-radius.csv

O segredo vem de arquivo (nunca da linha de comando). A senha dos usuários de
carga é a que a aba RADIUS mostra ao gerar a base de carga.
"""
import argparse
import csv
import hashlib
import hmac
import os
import socket
import statistics
import struct
import sys
import threading
import time
from pathlib import Path

ACCESS_REQUEST, ACCESS_ACCEPT, ACCESS_REJECT = 1, 2, 3
USER_NAME, USER_PASSWORD, NAS_IP_ADDRESS, NAS_PORT, MESSAGE_AUTHENTICATOR = 1, 2, 4, 5, 80


def encrypt_password(password: bytes, secret: bytes, authenticator: bytes) -> bytes:
    """RFC 2865, 5.2: c(i) = p(i) XOR MD5(S + c(i-1)), c(0) = autenticador do pedido."""
    padded = password + b"\x00" * (-len(password) % 16 or (16 if not password else 0))
    out, last = b"", authenticator
    for i in range(0, len(padded), 16):
        key = hashlib.md5(secret + last).digest()
        block = bytes(a ^ b for a, b in zip(padded[i:i + 16], key))
        out += block
        last = block
    return out


def attr(kind: int, value: bytes) -> bytes:
    return struct.pack("!BB", kind, len(value) + 2) + value


def build_request(ident: int, username: str, password: str, secret: bytes, nas_ip: str) -> tuple[bytes, bytes]:
    auth = os.urandom(16)
    attrs = (
        attr(USER_NAME, username.encode())
        + attr(USER_PASSWORD, encrypt_password(password.encode(), secret, auth))
        + attr(NAS_IP_ADDRESS, socket.inet_aton(nas_ip))
        + attr(NAS_PORT, struct.pack("!I", 0))
    )
    ma_offset = 20 + len(attrs) + 2
    attrs += attr(MESSAGE_AUTHENTICATOR, b"\x00" * 16)
    packet = struct.pack("!BBH", ACCESS_REQUEST, ident, 20 + len(attrs)) + auth + attrs
    mac = hmac.new(secret, packet, hashlib.md5).digest()
    packet = packet[:ma_offset] + mac + packet[ma_offset + 16:]
    return packet, auth


def check_response(data: bytes, ident: int, req_auth: bytes, secret: bytes) -> int | None:
    if len(data) < 20:
        return None
    code, rid, length = struct.unpack("!BBH", data[:4])
    if rid != ident or length > len(data):
        return None
    expected = hashlib.md5(data[:4] + req_auth + data[20:length] + secret).digest()
    if not hmac.compare_digest(expected, data[4:20]):
        return None  # resposta forjada ou segredo diferente
    return code


def worker(args, secret, jobs, results, lock):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)
    ident = 0
    while True:
        with lock:
            if not jobs:
                break
            n = jobs.pop()
        username = f"{args.prefix}{(n % args.users) + 1:05d}"
        ident = (ident + 1) % 256
        packet, auth = build_request(ident, username, args.password, secret, args.nas_ip)
        start = time.perf_counter()
        sock.sendto(packet, (args.server, args.port))
        outcome = "perdido"
        try:
            while True:
                data, _ = sock.recvfrom(4096)
                code = check_response(data, ident, auth, secret)
                if code is not None:
                    outcome = {ACCESS_ACCEPT: "aceito", ACCESS_REJECT: "negado"}.get(code, "outro")
                    break
        except socket.timeout:
            pass
        elapsed = (time.perf_counter() - start) * 1000
        with lock:
            results.append((outcome, elapsed))
    sock.close()


def run_once(args, secret: bytes, parallel: int) -> dict:
    jobs = list(range(args.requests))
    results, lock = [], threading.Lock()
    threads = [threading.Thread(target=worker, args=(args, secret, jobs, results, lock)) for _ in range(parallel)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_s = time.perf_counter() - t0
    answered = sorted(ms for outcome, ms in results if outcome != "perdido")
    count = {k: sum(1 for o, _ in results if o == k) for k in ("aceito", "negado", "perdido", "outro")}

    def pct(p):
        return round(answered[min(len(answered) - 1, int(len(answered) * p))], 2) if answered else None

    return {
        "cenario": args.label, "paralelismo": parallel, "pedidos": args.requests, **count,
        "duracao_s": round(total_s, 3), "vazao_req_s": round(len(answered) / total_s, 1) if total_s else 0,
        "latencia_media_ms": round(statistics.fmean(answered), 2) if answered else None,
        "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99),
        "perda_pct": round(100 * count["perdido"] / args.requests, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="192.168.100.10")
    ap.add_argument("--port", type=int, default=1812)
    ap.add_argument("--secret-file", required=True, type=Path)
    ap.add_argument("--password", required=True, help="senha dos usuários de carga (mostrada pela aba RADIUS)")
    ap.add_argument("--prefix", default="carga")
    ap.add_argument("--users", type=int, default=100, help="quantos usuários de carga existem (carga00001..)")
    ap.add_argument("--requests", type=int, default=1000)
    ap.add_argument("--parallel", type=int, nargs="+", default=[1, 10, 50])
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--nas-ip", default="192.168.100.20")
    ap.add_argument("--label", default="")
    ap.add_argument("--csv", type=Path, default=Path("resultados-radius.csv"))
    args = ap.parse_args()
    secret = args.secret_file.expanduser().read_text().strip().encode()

    new_file = not args.csv.exists()
    with args.csv.open("a", newline="", encoding="utf-8") as fh:
        out = None
        for parallel in args.parallel:
            for rep in range(1, args.repeat + 1):
                row = {"rodada": rep, **run_once(args, secret, parallel)}
                if out is None:
                    out = csv.DictWriter(fh, fieldnames=list(row))
                    if new_file:
                        out.writeheader()
                out.writerow(row)
                fh.flush()
                print(f"[{args.label}] paralelo={parallel} rodada={rep}: {row['vazao_req_s']} req/s, "
                      f"p95={row['p95_ms']} ms, aceitos={row['aceito']}, negados={row['negado']}, "
                      f"perdidos={row['perdido']}", flush=True)
    print(f"Resultados acrescentados em {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
