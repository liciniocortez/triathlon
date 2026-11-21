"""Funções compartilhadas: tokens criptografados, HTTP e configuração."""
import base64
import hashlib
import json
import os
from pathlib import Path

import requests
from cryptography.fernet import Fernet, InvalidToken

RAIZ = Path(__file__).resolve().parent.parent
ARQ_TOKENS = RAIZ / "tokens.enc"
ARQ_CONFIG = RAIZ / "config.json"
PASTA_DADOS = RAIZ / "data"

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"


def env(nome, obrigatorio=True):
    v = os.environ.get(nome, "").strip()
    if obrigatorio and not v:
        raise SystemExit(f"Falta o segredo {nome} no GitHub (Settings › Secrets and variables › Actions).")
    return v


def _fernet():
    chave = env("TOKEN_KEY")
    sal = os.environ.get("GITHUB_REPOSITORY", "painel-triathlon").encode()
    bruto = hashlib.pbkdf2_hmac("sha256", chave.encode(), sal, 300_000)
    return Fernet(base64.urlsafe_b64encode(bruto))


def ler_tokens():
    if not ARQ_TOKENS.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(ARQ_TOKENS.read_bytes()))
    except InvalidToken:
        raise SystemExit("Não foi possível abrir tokens.enc: o segredo TOKEN_KEY mudou. Conecte as contas de novo.")


def salvar_tokens(tokens):
    ARQ_TOKENS.write_bytes(_fernet().encrypt(json.dumps(tokens).encode()))


def ler_config():
    return json.loads(ARQ_CONFIG.read_text(encoding="utf-8"))


def post_token(url, dados):
    r = requests.post(url, data=dados, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Falha ao obter token em {url}: {r.status_code} {r.text[:300]}")
    return r.json()
