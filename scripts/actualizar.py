#!/usr/bin/env python3
import argparse, json, re, ssl, sys, urllib.error, urllib.request
from datetime import date, timedelta
from pathlib import Path

BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
SALIDA = Path(__file__).resolve().parent.parent / "docs" / "datos.json"
HOY = date.today()


def http(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/html"})
    try:
        return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError) and "bcra.gob.ar" in url:
            print("Aviso: certificado del BCRA no verificable, reintento sin verificar.", file=sys.stderr)
            ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=60, context=ctx).read().decode("utf-8", "replace")
        raise


def filas(j):
    r = j.get("results", j) if isinstance(j, dict) else j
    det = r[0]["detalle"] if r and isinstance(r[0], dict) and "detalle" in r[0] else r
    return [(x["fecha"], float(x["valor"])) for x in det if x.get("valor") is not None]


def serie(idv, desde, hasta):
    return filas(json.loads(http(f"{BCRA}/{idv}?desde={desde}&hasta={hasta}&limit=3000")))


def listar():
    todas, offset = [], 0
    while offset < 5000:
        j = json.loads(http(f"{BCRA}?limit=1000&offset={offset}"))
        res = j.get("results", [])
        if not res:
            break
        todas += res
        total = ((j.get("metadata") or {}).get("resultset") or {}).get("count", 0)
        offset += len(res)
        if offset >= total:
            break
    return todas


def buscar(lista, *opciones):
    for res in opciones:
        for v in lista:
            if all(re.search(r, v.get("descripcion", ""), re.I) for r in res):
                return v
    return None


def ultimo_valor(idv):
    s = serie(idv, str(HOY - timedelta(days=20)), str(HOY))
    return s[-1][1] if s else None


def elegir_badlar(lista):
    cand = [v for v in lista if re.search(r"badlar de bancos privados", v.get("descripcion", ""), re.I)]
    pesos, usd = [], []
    for v in cand:
        try:
            x = ultimo_valor(v["idVariable"])
        except Exception as e:
            print(f"  BADLAR {v['idVariable']}: no pude leerla ({e})", file=sys.stderr)
            continue
        print(f"  BADLAR candidata {v['idVariable']}: último valor {x}")
        if x is None:
            continue
        (pesos if x >= 5 else usd).append((x, v))
    pesos.sort(key=lambda t: t[0])
    usd.sort(key=lambda t: t[0])
    tna = tea = u = None
    if len(pesos) >= 2:
        ids = {v["idVariable"]: (x, v) for x, v in pesos}
        if 7 in ids and 35 in ids:
            a, b = ids[7], ids[35]
        else:
            a, b = pesos[0], pesos[-1]
        if a[0] > b[0]:
            a, b = b, a
        tna, tea = a[1], b[1]
    if usd:
        u = usd[0][1]
    return tna, tea, u


def diagnostico(lista):
    print(f"Total de series que devolvió el BCRA: {len(lista)}", file=sys.stderr)
    n = 0
    for v in lista:
        if re.search(r"badlar|cambio|d[oó]lar", v.get("descripcion", ""), re.I):
            print("  candidata:", v.get("idVariable"), v.get("descripcion"), file=sys.stderr)
            n += 1
            if n >= 40:
                break


def limpiar(s):
    return re.sub(r"\s+", " ", re.sub(r"&nbsp;", " ", re.sub(r"<[^>]*>", " ", s))).strip()


def num(s):
    return float(s.replace("%", "").replace(".", "").replace(",", ".").strip())


def bna():
    out = {}
    try:
        html = http("https://www.bna.com.ar/home/informacionalusuariofinanciero")
        pesos, usd = [], []
        for fila in map(limpiar, html.split("<tr")[1:]):
            r = re.search(r"De\s+(\d+)\s+a\s+(\d+)", fila, re.I)
            p = re.findall(r"\d+,\d+\s*%", fila)
            if not r or not p:
                continue
            fila_ = [int(r.group(1)), int(r.group(2)), num(p[0]), num(p[1])]
            if len(p) == 6:
                pesos.append(fila_)
            elif len(p) == 2:
                usd.append(fila_)
        if pesos:
            out["bna_pesos"] = pesos
        if usd:
            out["bna_usd"] = usd
    except Exception as e:
        print("Aviso: no pude leer las tasas del BNA:", e, file=sys.stderr)
    try:
        html = http("https://www.bna.com.ar/Personas")
        for fila in map(limpiar, html.split("<tr")[1:]):
            m = re.search(r"Dolar\s+U\.S\.A\s+([\d.,]+)\s+([\d.,]+)", fila, re.I)
            if m:
                out["bna_compra"], out["bna_venta"] = num(m.group(1)), num(m.group(2))
                break
    except Exception as e:
        print("Aviso: no pude leer el dólar del BNA:", e, file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde")
    a = ap.parse_args()

    db = json.loads(SALIDA.read_text(encoding="utf-8")) if SALIDA.exists() else {"datos": {}}
    datos = db.setdefault("datos", {})
    desde = a.desde or str(HOY - timedelta(days=(10 if datos else 3 * 365)))
    hasta = str(HOY)

    lista = listar()
    tna, tea, usd = elegir_badlar(lista)
    S = {
        "a3500": buscar(lista, ["mayorista", "referencia"], ["3500"]),
        "minorista_vendedor": buscar(lista, ["minorista", "vendedor"]),
        "badlar_tna": tna,
        "badlar_tea": tea,
        "usd": usd,
    }
    faltan = [k for k in ("a3500", "badlar_tea") if not S[k]]
    if faltan:
        diagnostico(lista)
        sys.exit(f"No encontré en el BCRA las series: {faltan}")

    for clave, v in S.items():
        if not v:
            print(f"Aviso: no encontré la serie '{clave}'", file=sys.stderr)
            continue
        print(f"Serie {clave}: {v['idVariable']} - {v['descripcion']}")
        try:
            for fecha, valor in serie(v["idVariable"], desde, hasta):
                if clave == "usd":
                    valor = ((1 + valor / 100 * 30 / 365) ** (365 / 30) - 1) * 100
                    clave_ = "usd_tea"
                else:
                    clave_ = clave
                datos.setdefault(fecha, {})[clave_] = round(valor, 4)
        except Exception as e:
            print(f"Aviso: falló la serie '{clave}': {e}", file=sys.stderr)

    extra = bna()
    if extra:
        datos.setdefault(str(HOY), {}).update(extra)

    db["actualizado"] = str(HOY)
    db["datos"] = dict(sorted(datos.items()))
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Listo: {len(db['datos'])} fechas en {SALIDA}")


if __name__ == "__main__":
    main()
