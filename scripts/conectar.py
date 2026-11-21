"""Conecta uma conta (uma vez só): troca o código de autorização por tokens e guarda em tokens.enc.

Uso (rodado pelo GitHub Actions › "Conectar conta"):
    python scripts/conectar.py strava <codigo>
    python scripts/conectar.py whoop  <codigo>
    python scripts/conectar.py garmin
"""
import os
import sys
import time

from comum import STRAVA_TOKEN_URL, WHOOP_TOKEN_URL, env, ler_tokens, post_token, salvar_tokens


def redirect_uri():
    dono, repo = os.environ["GITHUB_REPOSITORY"].split("/")
    return f"https://{dono.lower()}.github.io/{repo}/conectar.html"


def strava(codigo):
    t = post_token(STRAVA_TOKEN_URL, {
        "client_id": env("STRAVA_CLIENT_ID"), "client_secret": env("STRAVA_CLIENT_SECRET"),
        "code": codigo, "grant_type": "authorization_code",
    })
    nome = t.get("athlete", {}).get("firstname", "")
    return {"refresh_token": t["refresh_token"], "access_token": t["access_token"], "expires_at": t["expires_at"]}, nome


def whoop(codigo):
    t = post_token(WHOOP_TOKEN_URL, {
        "client_id": env("WHOOP_CLIENT_ID"), "client_secret": env("WHOOP_CLIENT_SECRET"),
        "code": codigo, "grant_type": "authorization_code", "redirect_uri": redirect_uri(),
    })
    print(f"Whoop respondeu: campos={sorted(t.keys())} escopos='{t.get('scope')}' "
          f"refresh={'sim' if t.get('refresh_token') else 'NÃO'} expira_em={t.get('expires_in')}")
    if not t.get("refresh_token"):
        raise SystemExit("O Whoop não devolveu refresh_token. Confira se o app tem o escopo 'offline' e autorize de novo.")
    return {"refresh_token": t["refresh_token"], "access_token": t["access_token"],
            "expires_at": int(time.time()) + int(t.get("expires_in", 3600))}, ""


def garmin():
    from garminconnect import Garmin
    g = Garmin(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
    g.login()  # só testa: a atualização diária faz login com o e-mail e a senha guardados nos segredos
    try:
        nome = g.get_full_name() or ""
    except Exception:
        nome = ""
    return {"ativo": True}, nome


def main():
    servico = sys.argv[1].lower()
    codigo = sys.argv[2].strip() if len(sys.argv) > 2 else ""
    if servico in ("strava", "whoop") and not codigo:
        raise SystemExit("Cole o código mostrado na página conectar.html.")
    tokens = ler_tokens()
    if servico == "strava":
        tokens["strava"], nome = strava(codigo)
    elif servico == "whoop":
        tokens["whoop"], nome = whoop(codigo)
    elif servico == "garmin":
        tokens["garmin"], nome = garmin()
    else:
        raise SystemExit("Serviço inválido. Use strava, whoop ou garmin.")
    salvar_tokens(tokens)
    print(f"✅ {servico.capitalize()} conectado {('(' + nome + ')') if nome else ''}.")


if __name__ == "__main__":
    main()
