"""Análises derivadas: zonas de FC, eficiência aeróbica, projeção de prova e equipamentos."""
import json
from datetime import date, datetime, timedelta

import requests

from comum import PASTA_DADOS

ARQ_ZONAS = PASTA_DADOS / "zonas.json"
STRAVA = "https://www.strava.com/api/v3"


# ---------------------------------------------------------------- zonas de FC (streams do Strava, com cache)
def _zona(fc, limites):
    for i, lim in enumerate(limites):
        if fc <= lim:
            return i
    return len(limites)


def zonas_fc(token, ativ, cfg, hoje, log, max_novas=80):
    """Tempo em cada zona (Z1..Z5) por atividade. Busca no máximo `max_novas` por dia (limite da API)."""
    zcfg = cfg.get("zonas_fc", {})
    cache = json.loads(ARQ_ZONAS.read_text()) if ARQ_ZONAS.exists() else {}
    corte = (hoje - timedelta(days=84)).isoformat()
    pendentes = [a for a in ativ if a["date"] >= corte and a["type"] in ("run", "bike", "swim")
                 and a.get("hr") and str(a["id"]) not in cache]
    novas = 0
    for a in pendentes[:max_novas]:
        r = requests.get(f"{STRAVA}/activities/{a['id']}/streams", headers={"Authorization": f"Bearer {token}"},
                         params={"keys": "heartrate,time", "key_by_type": "true"}, timeout=30)
        if r.status_code == 429:
            log("Strava: limite de requisições atingido; o resto das zonas fica para amanhã.")
            break
        if r.status_code != 200:
            cache[str(a["id"])] = None
            continue
        j = r.json()
        fcs, ts = (j.get("heartrate") or {}).get("data"), (j.get("time") or {}).get("data")
        if not fcs or not ts:
            cache[str(a["id"])] = None
            continue
        lim = zcfg.get(a["type"]) or zcfg.get("padrao")
        seg = [0, 0, 0, 0, 0]
        for i in range(len(ts) - 1):
            seg[_zona(fcs[i], lim)] += min(ts[i + 1] - ts[i], 30)
        cache[str(a["id"])] = seg
        novas += 1
    # mantém só o que ainda interessa
    ids = {str(a["id"]) for a in ativ if a["date"] >= corte}
    cache = {k: v for k, v in cache.items() if k in ids}
    ARQ_ZONAS.write_text(json.dumps(cache))
    log(f"Zonas de FC: {novas} atividades novas, {sum(1 for v in cache.values() if v)} no total.")

    # semanas (segunda a domingo), últimas 12
    seg_hoje = hoje - timedelta(days=hoje.weekday())
    semanas = []
    for w in range(11, -1, -1):
        ini = seg_hoje - timedelta(weeks=w)
        fim = ini + timedelta(days=7)
        tot = {"run": [0] * 5, "bike": [0] * 5, "swim": [0] * 5}
        for a in ativ:
            d = date.fromisoformat(a["date"][:10])
            z = cache.get(str(a["id"]))
            if z and ini <= d < fim:
                tot[a["type"]] = [x + y for x, y in zip(tot[a["type"]], z)]
        semanas.append({"inicio": ini.isoformat(), **{k: [round(x / 60) for x in v] for k, v in tot.items()}})
    return semanas


# ---------------------------------------------------------------- eficiência aeróbica
def eficiencia(ativ, hoje):
    """Ritmo de corrida a 150 bpm e potência de bike a 130 bpm, por mês (12 meses)."""
    meses = {}
    for a in ativ:
        if a.get("race") or not a.get("hr") or a["min"] < 25:
            continue
        m = a["date"][:7]
        if a["type"] == "run" and a.get("km") and a["km"] > 3:
            ef = (a["km"] * 1000 / a["min"]) / a["hr"]  # m/min por bpm
            meses.setdefault(m, {}).setdefault("run", []).append((ef, a["min"]))
        elif a["type"] == "bike" and a.get("watts") and a["min"] >= 40:
            meses.setdefault(m, {}).setdefault("bike", []).append((a["watts"] / a["hr"], a["min"]))
    saida = []
    for m in sorted(meses)[-12:]:
        item = {"mes": m}
        for tipo, ref in (("run", 150), ("bike", 130)):
            v = meses[m].get(tipo)
            if v and len(v) >= 2:
                ef = sum(e * p for e, p in v) / sum(p for _, p in v)  # média ponderada pela duração
                item[tipo] = round(1000 / (ef * ref), 3) if tipo == "run" else round(ef * ref)
                item[tipo + "_n"] = len(v)
        saida.append(item)
    return saida


# ---------------------------------------------------------------- projeção da próxima prova
def _mmss(t):
    p = [int(x) for x in t.split(":")]
    return p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]


def projecao(ativ, garmin, cfg, dias, hoje):
    prox = [p for p in cfg.get("proximas", []) if p.get("tipo") == "tri" and date.fromisoformat(p["data"]) >= hoje]
    if not prox:
        return None
    prova = sorted(prox, key=lambda p: p["data"])[0]
    lim = cfg.get("limiares", {})
    pj = cfg.get("projecao", {})
    ftp = lim.get("ftp") or (garmin or {}).get("ftp")
    fi_bike = pj.get("fi_bike", 0.80)
    fator_corrida = pj.get("fator_corrida", 1.18)
    transicoes = pj.get("transicoes_min", 8) * 60

    # natação: recorde de 1.500 m (Garmin) + 5% para águas abertas; senão, o CSS
    prs = {r["typeId"]: r["value"] for r in (garmin or {}).get("prs", []) if r.get("value")}
    if 25 in prs:
        ritmo_nat = prs[25] / 15 * 1.05
        fonte_nat = "recorde de 1.500 m + 5% (águas abertas)"
    else:
        ritmo_nat = _mmss(lim.get("css_100m", "2:30")) * 1.05
        fonte_nat = "CSS + 5%"
    nat = ritmo_nat * 19

    # bike: velocidade ∝ potência^(1/3), calibrada no pedal da última 70.3
    ref = None
    for p in sorted(cfg.get("provas", []), key=lambda p: p["data"], reverse=True):
        if p.get("categoria") == "tri":
            c = [a for a in ativ if a["date"][:10] == p["data"] and a["type"] == "bike" and a.get("watts")
                 and (a.get("km") or 0) > 60]
            if c:
                b = c[0]
                ref = {"prova": p["nome"], "kmh": b["km"] / (b["min"] / 60), "watts": b["watts"]}
                break
    if not ref:
        ref = {"prova": "padrão", "kmh": 30.0, "watts": 180}
    k = ref["kmh"] / ref["watts"] ** (1 / 3)
    pot = (ftp or ref["watts"]) * fi_bike
    kmh = k * pot ** (1 / 3)
    bike = 90 / kmh * 3600

    # corrida: ritmo de limiar × fator para meia após 90 km de bike
    ritmo_cor = _mmss(lim.get("ritmo_limiar_corrida", "5:00")) * fator_corrida
    corrida = ritmo_cor * 21.1

    total = nat + transicoes + bike + corrida
    meta_s = None
    if prova.get("meta", "").startswith("sub-"):
        h, m = prova["meta"][4:].split(":")[:2]  # meta em h:mm
        meta_s = int(h) * 3600 + int(m) * 60
    falta_bike = None
    if meta_s:
        bike_nec = meta_s - nat - transicoes - corrida
        if bike_nec > 0:
            kmh_nec = 90 / (bike_nec / 3600)
            falta_bike = {"tempo": round(bike_nec), "kmh": round(kmh_nec, 1),
                          "watts": round((kmh_nec / k) ** 3)}

    # condicionamento no dia da prova: mantém a média de TSS das últimas 4 semanas, polimento de 10 dias a 50%
    media = sum(d["tss"] for d in dias[-28:]) / 28 if dias else 0
    ctl, atl = (dias[-1]["ctl"], dias[-1]["atl"]) if dias else (0, 0)
    dia_prova = date.fromisoformat(prova["data"])
    curva, d = [], hoje
    while d < dia_prova:
        d += timedelta(days=1)
        tss = media * (0.5 if (dia_prova - d).days <= 10 else 1)
        if d == dia_prova:
            tss = 0
        ctl += (tss - ctl) / 42
        atl += (tss - atl) / 7
        curva.append({"date": d.isoformat(), "ctl": round(ctl, 1), "atl": round(atl, 1)})
    return {
        "prova": prova["nome"], "data": prova["data"], "meta": prova.get("meta", ""), "meta_s": meta_s,
        "natacao": round(nat), "transicoes": transicoes, "bike": round(bike), "corrida": round(corrida),
        "total": round(total),
        "ritmo_nat": round(ritmo_nat), "kmh": round(kmh, 1), "watts": round(pot), "ritmo_cor": round(ritmo_cor),
        "falta_bike": falta_bike, "tss_medio": round(media),
        "premissas": {"natacao": fonte_nat, "bike": f"{round(fi_bike * 100)}% do FTP ({ftp} W), velocidade calibrada no pedal do {ref['prova']}",
                      "corrida": f"ritmo de limiar ({lim.get('ritmo_limiar_corrida')}/km) × {fator_corrida}",
                      "transicoes": f"{transicoes // 60} min (T1 + T2)"},
        "curva": curva,
    }


# ---------------------------------------------------------------- equipamentos
def equipamentos(token):
    r = requests.get(f"{STRAVA}/athlete", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    j = r.json()
    itens = []
    for tipo, chave in (("run", "shoes"), ("bike", "bikes")):
        for g in j.get(chave) or []:
            itens.append({"type": tipo, "name": g.get("name") or g.get("nickname") or "—",
                          "km": round((g.get("distance") or 0) / 1000),
                          "primary": bool(g.get("primary")), "retired": bool(g.get("retired"))})
    return itens


# ---------------------------------------------------------------- geografia
ARQ_LUGARES = PASTA_DADOS / "lugares.json"
BASE = (-5.53, -47.48)  # Imperatriz


def _dist_km(a, b):
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def geografia(ativ, log, max_geocode=25):
    import time
    pontos = {}
    for a in ativ:
        ll = a.get("latlng")
        if ll:
            k = f"{ll[0]:.1f},{ll[1]:.1f}"
            pontos[k] = pontos.get(k, 0) + 1
    if not pontos:
        return None
    cache = json.loads(ARQ_LUGARES.read_text()) if ARQ_LUGARES.exists() else {}
    feitos = 0
    for k, _ in sorted(pontos.items(), key=lambda x: -x[1]):
        if k in cache or feitos >= max_geocode:
            continue
        lat, lng = k.split(",")
        try:
            r = requests.get("https://nominatim.openstreetmap.org/reverse",
                             params={"lat": lat, "lon": lng, "format": "json", "zoom": 10, "accept-language": "pt-BR"},
                             headers={"User-Agent": "painel-triathlon-liciniocortez/1.0 (github.com/liciniocortez/triathlon)"},
                             timeout=20)
            ad = r.json().get("address", {}) if r.status_code == 200 else {}
            cache[k] = {"cidade": ad.get("city") or ad.get("town") or ad.get("municipality") or ad.get("village") or ad.get("county") or "",
                        "estado": ad.get("state", ""), "pais": ad.get("country", ""), "cc": (ad.get("country_code") or "").upper()}
        except Exception:
            cache[k] = {"cidade": "", "estado": "", "pais": "", "cc": ""}
        feitos += 1
        time.sleep(1.1)  # política de uso do Nominatim
    ARQ_LUGARES.write_text(json.dumps(cache, ensure_ascii=False))
    # agrega por cidade
    cidades = {}
    for k, n in pontos.items():
        c = cache.get(k) or {}
        nome = c.get("cidade") or ""
        if not nome:
            continue
        chave = (nome, c.get("estado", ""), c.get("pais", ""))
        cidades[chave] = cidades.get(chave, 0) + n
    top = [{"cidade": c[0], "estado": c[1], "pais": c[2], "n": n} for c, n in sorted(cidades.items(), key=lambda x: -x[1])[:5]]
    coords = [tuple(map(float, k.split(","))) for k in pontos]
    log(f"Geografia: {len(pontos)} locais, {feitos} geocodificados agora.")
    return {
        "pontos": [[*map(float, k.split(",")), n] for k, n in pontos.items()],
        "locais": len(cidades) or len(pontos),
        "paises": len({(cache.get(k) or {}).get("pais") for k in pontos if (cache.get(k) or {}).get("pais")}),
        "mais_longe_km": round(max(_dist_km(BASE, c) for c in coords)),
        "total": sum(pontos.values()),
    }
