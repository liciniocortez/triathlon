"""Atualização diária: busca Strava, Whoop, Garmin e TrainingPeaks e gera data/data.json para o site."""
import json
import re
import time
import traceback
from datetime import date, datetime, timedelta, timezone

import requests

from comum import (PASTA_DADOS, STRAVA_TOKEN_URL, WHOOP_TOKEN_URL, env, ler_config, ler_tokens,
                   post_token, salvar_tokens)

BRT = timezone(timedelta(hours=-3))
AGORA = datetime.now(BRT)
HOJE = AGORA.date()
ARQ_ATIV = PASTA_DADOS / "atividades.json"
ARQ_SAIDA = PASTA_DADOS / "data.json"
avisos = []


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ Strava
TIPOS_STRAVA = {
    "Swim": "swim",
    "Ride": "bike", "VirtualRide": "bike", "GravelRide": "bike", "MountainBikeRide": "bike", "EBikeRide": "bike",
    "Run": "run", "TrailRun": "run", "VirtualRun": "run",
    "WeightTraining": "str", "Workout": "str", "Crossfit": "str", "Yoga": "str", "Pilates": "str",
}


def strava_token(tokens):
    t = tokens["strava"]
    if t.get("expires_at", 0) > time.time() + 120:
        return t["access_token"]
    novo = post_token(STRAVA_TOKEN_URL, {
        "client_id": env("STRAVA_CLIENT_ID"), "client_secret": env("STRAVA_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"],
    })
    tokens["strava"] = {"access_token": novo["access_token"], "refresh_token": novo["refresh_token"],
                        "expires_at": novo["expires_at"]}
    return novo["access_token"]


def calc_tss(tipo, horas, a, cfg):
    """TSS estimado. Bike com potência: NP/FTP. Natação sem FC: ritmo vs CSS. Demais: FC vs limiar."""
    lim = cfg.get("limiares", {})
    np_ = a.get("weighted_average_watts") if a.get("device_watts") else None
    if tipo == "bike" and np_ and lim.get("ftp"):
        fi = np_ / lim["ftp"]
        return round(horas * fi * fi * 100)
    fc = a.get("average_heartrate")
    lthr = {"run": lim.get("lthr_corrida"), "bike": lim.get("lthr_bike"), "swim": lim.get("lthr_natacao")}.get(tipo)
    if fc and lthr:
        fi = min(fc / lthr, 1.15)
        return round(horas * fi * fi * 100)
    if tipo == "swim" and a.get("distance") and lim.get("css_100m"):
        m, s = map(int, lim["css_100m"].split(":"))
        css = m * 60 + s
        ritmo = a["moving_time"] / (a["distance"] / 100)
        fi = min(css / ritmo, 1.15)
        return round(horas * fi ** 3 * 100)
    padrao = {"run": .78, "bike": .7, "swim": .72, "str": .55}[tipo]
    return round(horas * padrao * padrao * 100)


def normalizar_strava(a, cfg):
    tipo = TIPOS_STRAVA.get(a.get("sport_type") or a.get("type"))
    if not tipo:
        return None
    minutos = a["moving_time"] / 60
    km = a.get("distance", 0) / 1000
    item = {
        "id": a["id"], "type": tipo, "name": a.get("name", ""),
        "date": a["start_date_local"].replace("Z", ""),
        "min": round(minutos, 1), "km": round(km, 2 if tipo == "swim" else 1) if km > 0.05 else None,
        "hr": round(a["average_heartrate"]) if a.get("average_heartrate") else None,
        "max_hr": round(a["max_heartrate"]) if a.get("max_heartrate") else None,
        "elev": round(a.get("total_elevation_gain") or 0),
        "tss": calc_tss(tipo, minutos / 60, a, cfg),
        "race": a.get("workout_type") in (1, 11),
        "elapsed": a.get("elapsed_time"),
    }
    if tipo == "bike" and a.get("average_watts"):
        item["watts"] = round(a.get("weighted_average_watts") or a["average_watts"])
    item["kcal"] = round(a["kilojoules"]) if a.get("kilojoules") else round(minutos * 8)
    return item


def buscar_strava(tokens, cfg):
    antigas = json.loads(ARQ_ATIV.read_text()) if ARQ_ATIV.exists() else []
    por_id = {a["id"]: a for a in antigas}
    if antigas:
        ultimo = max(datetime.fromisoformat(a["date"]) for a in antigas)
        depois = ultimo.replace(tzinfo=BRT) - timedelta(days=5)
    else:
        depois = AGORA - timedelta(days=cfg.get("dias_de_historico", 365))
    token = strava_token(tokens)
    pagina, novas = 1, 0
    while True:
        r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"after": int(depois.timestamp()), "per_page": 200, "page": pagina}, timeout=30)
        r.raise_for_status()
        lote = r.json()
        if not lote:
            break
        for a in lote:
            n = normalizar_strava(a, cfg)
            if n:
                por_id[n["id"]] = n
                novas += 1
        pagina += 1
    ativ = sorted(por_id.values(), key=lambda a: a["date"], reverse=True)
    ARQ_ATIV.write_text(json.dumps(ativ, ensure_ascii=False))
    log(f"Strava: {novas} atividades recebidas, {len(ativ)} no histórico.")
    return ativ


# ------------------------------------------------------------------ Whoop
WHOOP_API = "https://api.prod.whoop.com/developer/v2"


def whoop_token(tokens):
    t = tokens["whoop"]
    if t.get("expires_at", 0) > time.time() + 120:
        return t["access_token"]
    novo = post_token(WHOOP_TOKEN_URL, {
        "client_id": env("WHOOP_CLIENT_ID"), "client_secret": env("WHOOP_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": t["refresh_token"], "scope": "offline",
    })
    tokens["whoop"] = {"access_token": novo["access_token"], "refresh_token": novo.get("refresh_token", t["refresh_token"]),
                       "expires_at": int(time.time()) + int(novo.get("expires_in", 3600))}
    return novo["access_token"]


def whoop_lista(token, caminho, dias):
    inicio = (AGORA - timedelta(days=dias)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    itens, prox = [], None
    while True:
        params = {"start": inicio, "limit": 25}
        if prox:
            params["nextToken"] = prox
        r = requests.get(f"{WHOOP_API}{caminho}", headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        itens += j.get("records", [])
        prox = j.get("next_token")
        if not prox:
            return itens


def data_local(iso, offset="-03:00"):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    sinal = -1 if offset.startswith("-") else 1
    h, m = map(int, offset[1:].split(":"))
    return (dt + sinal * timedelta(hours=h, minutes=m)).date().isoformat()


def buscar_whoop(tokens):
    token = whoop_token(tokens)
    dias = 45
    sonos = {s["id"]: s for s in whoop_lista(token, "/activity/sleep", dias) if not s.get("nap")}
    ciclos = whoop_lista(token, "/cycle", dias)
    recs = whoop_lista(token, "/recovery", dias)
    por_dia = {}
    for s in sonos.values():
        sc = (s.get("score") or {}).get("stage_summary") or {}
        if not sc:
            continue
        horas = (sc.get("total_in_bed_time_milli", 0) - sc.get("total_awake_time_milli", 0)) / 3.6e6
        d = data_local(s["end"], s.get("timezone_offset") or "-03:00")
        por_dia.setdefault(d, {})["sleep"] = round(horas, 1)
    for r in recs:
        sc = r.get("score") or {}
        if r.get("score_state") != "SCORED" or not sc:
            continue
        s = sonos.get(r.get("sleep_id"))
        d = data_local(s["end"], s.get("timezone_offset") or "-03:00") if s else data_local(r["created_at"])
        por_dia.setdefault(d, {}).update({
            "rec": round(sc["recovery_score"]), "hrv": round(sc["hrv_rmssd_milli"]), "rhr": round(sc["resting_heart_rate"]),
        })
    for c in ciclos:
        sc = c.get("score") or {}
        if sc.get("strain") is None:
            continue
        d = data_local(c["start"], c.get("timezone_offset") or "-03:00")
        por_dia.setdefault(d, {})["strain"] = round(sc["strain"], 1)
    saida = [{"date": d, **v} for d, v in sorted(por_dia.items()) if "rec" in v][-30:]
    for x in saida:
        x.setdefault("sleep", 0)
        x.setdefault("strain", 0)
    log(f"Whoop: {len(saida)} dias.")
    return saida


# ------------------------------------------------------------------ Garmin
def procurar(obj, chave):
    if isinstance(obj, dict):
        if chave in obj and obj[chave] is not None:
            return obj[chave]
        for v in obj.values():
            r = procurar(v, chave)
            if r is not None:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = procurar(v, chave)
            if r is not None:
                return r
    return None


def buscar_garmin(tokens, cfg):
    from garminconnect import Garmin
    g = Garmin()
    g.login(tokens["garmin"]["garth"])
    out = {}
    mm = g.get_max_metrics(HOJE.isoformat())
    gen = procurar(mm, "generic")
    cyc = procurar(mm, "cycling")
    if gen:
        out["vo2run"] = round(procurar(gen, "vo2MaxPreciseValue") or procurar(gen, "vo2MaxValue") or 0) or None
    if cyc:
        out["vo2bike"] = round(procurar(cyc, "vo2MaxPreciseValue") or procurar(cyc, "vo2MaxValue") or 0) or None
    try:
        lt = g.get_lactate_threshold(latest=True)
        vel, fc = procurar(lt, "speed"), procurar(lt, "heartRate")
        if vel:
            vel = vel * 10 if vel < 1 else vel
            seg = 1000 / vel
            out["lt_pace"] = f"{int(seg // 60)}:{int(seg % 60):02d}"
        if fc:
            out["lt_hr"] = round(fc)
        ftp, peso = procurar(lt, "functionalThresholdPower"), procurar(lt, "weight")
        if ftp:
            out["ftp"] = round(ftp)
            if peso:
                out["wkg"] = round(ftp / (peso / 1000 if peso > 300 else peso), 1)
    except Exception as e:
        avisos.append(f"Garmin limiar: {e}")
    tokens["garmin"]["garth"] = g.garth.dumps()
    log(f"Garmin: {out}")
    return out


# ------------------------------------------------------------------ TrainingPeaks (.ics)
def tipo_por_texto(txt):
    t = txt.lower()
    for chaves, tipo in ((("swim", "nata", "nado", "piscina", "águas", "aguas"), "swim"),
                         (("bike", "ride", "pedal", "cicl", "rolo", "brick"), "bike"),
                         (("run", "corr", "trote", "longão", "longao"), "run"),
                         (("strength", "força", "forca", "academia", "muscul", "funcional"), "str")):
        if any(c in t for c in chaves):
            return tipo
    return None


def duracao_min(txt):
    m = re.search(r"(?:planned|planejad|dura)[^0-9]{0,20}(\d{1,2}):(\d{2})(?::(\d{2}))?", txt, re.I) \
        or re.search(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", txt)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return h * 60 + mi
    m = re.search(r"(\d{2,3})\s*min", txt, re.I)
    return int(m.group(1)) if m else None


def ler_ics(texto):
    texto = re.sub(r"\r?\n[ \t]", "", texto)
    eventos = []
    for bloco in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", texto, re.S):
        campos = {}
        for linha in bloco.strip().splitlines():
            if ":" not in linha:
                continue
            k, v = linha.split(":", 1)
            campos[k.split(";")[0].upper()] = v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").strip()
        eventos.append(campos)
    return eventos


def ics_data(v):
    v = v.strip()
    if len(v) == 8:
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(BRT)
    return dt.date()


def buscar_tp():
    url = env("TP_ICAL_URL").replace("webcal://", "https://")
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    plano = []
    for e in ler_ics(r.text):
        if "DTSTART" not in e:
            continue
        d = ics_data(e["DTSTART"])
        if not (HOJE - timedelta(days=6) <= d <= HOJE + timedelta(days=15)):
            continue
        titulo, desc = e.get("SUMMARY", "Treino"), e.get("DESCRIPTION", "")
        tipo = tipo_por_texto(titulo) or tipo_por_texto(desc)
        if not tipo:
            continue  # eventos que não são treino (provas, notas)
        mins = duracao_min(desc) or duracao_min(titulo)
        if not mins and len(e.get("DTEND", "")) > 8 and len(e["DTSTART"]) > 8:
            a = datetime.strptime(e["DTSTART"][:15], "%Y%m%dT%H%M%S")
            b = datetime.strptime(e["DTEND"][:15], "%Y%m%dT%H%M%S")
            mins = int((b - a).total_seconds() / 60) or None
        plano.append({"date": d.isoformat(), "type": tipo, "name": titulo, "min": mins, "desc": desc[:400]})
    plano.sort(key=lambda p: p["date"])
    log(f"TrainingPeaks: {len(plano)} treinos planejados.")
    return plano


# ------------------------------------------------------------------ PMC e provas
def pmc(ativ):
    por_dia = {}
    for a in ativ:
        d = a["date"][:10]
        por_dia[d] = por_dia.get(d, 0) + a["tss"]
    if not por_dia:
        return []
    d = min(date.fromisoformat(x) for x in por_dia)
    ctl = atl = 0.0
    dias = []
    while d <= HOJE:
        tss = por_dia.get(d.isoformat(), 0)
        ctl += (tss - ctl) / 42
        atl += (tss - atl) / 7
        dias.append({"date": d.isoformat(), "tss": tss, "ctl": round(ctl, 1), "atl": round(atl, 1)})
        d += timedelta(days=1)
    return dias[-200:]


MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def hms(seg):
    seg = int(round(seg))
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def relogio_corrida(ativ, dia, km=None):
    cands = [a for a in ativ if a["type"] == "run" and a["date"][:10] == dia and a.get("km")]
    if km:
        cands.sort(key=lambda a: abs(a["km"] - km))
    if not cands:
        return None
    a = cands[0]
    return {"pace": hms(a["min"] * 60 / a["km"]), "hr": a.get("hr") or "—", "max": a.get("max_hr") or "—", "id": a["id"]}


def montar_provas(ativ, cfg):
    provas = []
    datas_config = set()
    for p in cfg.get("provas", []):
        d = date.fromisoformat(p["data"])
        datas_config.add(p["data"])
        dist = p.get("distancias", {})
        of = p.get("oficial", {})
        item = {"cat": p.get("categoria", "tri"), "name": p["nome"], "place": p.get("local", ""),
                "date": f"{MESES[d.month - 1]} {d.year}", "tag": p.get("destaque", ""), "total": of.get("total", "")}
        if item["cat"] == "tri":
            item["splits"] = {"swim": [dist.get("natacao", 0), of.get("natacao", "")], "t1": of.get("t1", ""),
                              "bike": [dist.get("bike", 0), of.get("bike", "")], "t2": of.get("t2", ""),
                              "run": [dist.get("corrida", 0), of.get("corrida", "")]}
        else:
            item["run"] = [dist.get("corrida", 0), of.get("total", "")]
        item["watch"] = relogio_corrida(ativ, p["data"], dist.get("corrida")) or {"pace": "—", "hr": "—", "max": "—"}
        provas.append((p["data"], item))
    # corridas marcadas como "Prova" no Strava e ainda não cadastradas
    for a in ativ:
        if a.get("race") and a["type"] == "run" and a["date"][:10] not in datas_config:
            d = date.fromisoformat(a["date"][:10])
            provas.append((a["date"][:10], {
                "cat": "run", "name": a["name"], "place": "", "date": f"{MESES[d.month - 1]} {d.year}",
                "tag": "Do Strava · marcada como prova", "total": hms(a.get("elapsed") or a["min"] * 60),
                "run": [a["km"], hms(a.get("elapsed") or a["min"] * 60)],
                "watch": relogio_corrida(ativ, a["date"][:10], a["km"])}))
    provas.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in provas]


# ------------------------------------------------------------------ principal
def etapa(nome, func, *args, padrao=None):
    try:
        return func(*args)
    except Exception as e:
        traceback.print_exc()
        avisos.append(f"{nome}: {e}")
        return padrao


def main():
    PASTA_DADOS.mkdir(exist_ok=True)
    cfg = ler_config()
    tokens = ler_tokens()
    anterior = json.loads(ARQ_SAIDA.read_text()) if ARQ_SAIDA.exists() else {}

    ativ = etapa("Strava", buscar_strava, tokens, cfg) if "strava" in tokens else None
    if ativ is None:
        ativ = json.loads(ARQ_ATIV.read_text()) if ARQ_ATIV.exists() else []
        if "strava" not in tokens:
            avisos.append("Strava ainda não conectado.")
    whoop = etapa("Whoop", buscar_whoop, tokens) if "whoop" in tokens else None
    garmin = etapa("Garmin", buscar_garmin, tokens, cfg) if "garmin" in tokens else None
    plano = etapa("TrainingPeaks", buscar_tp) if env("TP_ICAL_URL", False) else None

    # mantém o último valor bom se uma fonte falhar hoje
    whoop = whoop if whoop is not None else anterior.get("whoop", [])
    garmin = garmin if garmin is not None else anterior.get("garmin")
    plano = plano if plano is not None else anterior.get("plan", [])
    lim = cfg.get("limiares", {})
    if garmin is not None and lim.get("ftp") and not garmin.get("ftp"):
        garmin["ftp"] = lim["ftp"]

    inicio_ano = f"{HOJE.year}-01-01"
    corte = (HOJE - timedelta(days=120)).isoformat()
    publicas = [{k: v for k, v in a.items() if k not in ("elapsed",)} for a in ativ
                if a["date"] >= min(inicio_ano, corte)]

    saida = {
        "updated": AGORA.isoformat(timespec="minutes"),
        "acts": publicas,
        "days": pmc(ativ),
        "whoop": whoop,
        "plan": plano,
        "garmin": garmin,
        "races": montar_provas(ativ, cfg),
        "warnings": avisos,
    }
    PASTA_DADOS.mkdir(exist_ok=True)
    ARQ_SAIDA.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")))
    salvar_tokens(tokens)
    log(f"OK · {len(publicas)} atividades no site.")
    if avisos:
        log("Avisos:\n- " + "\n- ".join(avisos))


if __name__ == "__main__":
    main()
