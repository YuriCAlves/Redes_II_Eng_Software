"""Hashes de senha aceitos pelo módulo pap do FreeRADIUS (o console nunca guarda a senha).

 - SSHA2-256-Password: base64(SHA-256(senha + sal) + sal), sal de 8 bytes;
 - Crypt-Password: SHA-512 crypt ("$6$"), o mesmo formato do /etc/shadow, que o
   FreeRADIUS confere com crypt() da glibc. Implementado aqui porque o módulo
   `crypt` saiu da biblioteca padrão no Python 3.13.

Os dois esquemas existem para o experimento de desempenho do serviço exclusivo
(custo do hash x vazão de autenticação).
"""
import base64
import hashlib
import hmac
import os
import secrets

_B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SALT_CHARS = _B64
_ORDER = (
    (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48),
    (28, 49, 7), (50, 8, 29), (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13),
    (56, 14, 35), (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41),
)

SCHEMES = ("ssha2-256", "sha512-crypt")


def _b64_24(b2: int, b1: int, b0: int, n: int) -> str:
    w = (b2 << 16) | (b1 << 8) | b0
    out = []
    for _ in range(n):
        out.append(_B64[w & 0x3F])
        w >>= 6
    return "".join(out)


def sha512_crypt(password: str, salt: str | None = None, rounds: int = 5000) -> str:
    """SHA-512 crypt (Drepper, "Unix crypt using SHA-256 and SHA-512")."""
    rounds = max(1000, min(999_999_999, int(rounds)))
    salt = (salt or "".join(secrets.choice(_SALT_CHARS) for _ in range(16)))[:16]
    p, s = password.encode(), salt.encode()

    b = hashlib.sha512(p + s + p).digest()
    a = hashlib.sha512(p + s)
    n = len(p)
    while n > 64:
        a.update(b)
        n -= 64
    a.update(b[:n])
    n = len(p)
    while n > 0:
        a.update(b if n & 1 else p)
        n >>= 1
    c = a.digest()

    dp = hashlib.sha512(p * len(p)).digest()
    pseq = (dp * (len(p) // 64 + 1))[: len(p)]
    ds = hashlib.sha512(s * (16 + c[0])).digest()
    sseq = (ds * (len(s) // 64 + 1))[: len(s)]

    for i in range(rounds):
        h = hashlib.sha512(pseq if i & 1 else c)
        if i % 3:
            h.update(sseq)
        if i % 7:
            h.update(pseq)
        h.update(c if i & 1 else pseq)
        c = h.digest()

    encoded = "".join(_b64_24(c[x], c[y], c[z], 4) for x, y, z in _ORDER) + _b64_24(0, 0, c[63], 2)
    prefix = "$6$" if rounds == 5000 else f"$6$rounds={rounds}$"
    return f"{prefix}{salt}${encoded}"


def ssha2_256(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(8)
    return base64.b64encode(hashlib.sha256(password.encode() + salt).digest() + salt).decode()


def make(password: str, scheme: str = "ssha2-256", rounds: int = 5000) -> tuple[str, str]:
    """Devolve (atributo de verificação do FreeRADIUS, valor)."""
    if scheme == "sha512-crypt":
        return "Crypt-Password", sha512_crypt(password, rounds=rounds)
    if scheme == "ssha2-256":
        return "SSHA2-256-Password", ssha2_256(password)
    raise ValueError("esquema de senha desconhecido")


def verify_ssha2_256(password: str, value: str) -> bool:
    raw = base64.b64decode(value)
    digest, salt = raw[:32], raw[32:]
    return hmac.compare_digest(hashlib.sha256(password.encode() + salt).digest(), digest)
