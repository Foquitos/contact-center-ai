#!/usr/bin/env python3
"""Usuarios sin luz de Voltara y Norluz según el ENRE -> planificacion.CorteEnre.

POR QUÉ
-------
Las llamadas de EMERGENCIAS de Voltara son reclamos por falta de luz, y el ENRE
publica cuántos usuarios están sin servicio. Medido contra la demanda del cliente
(ene-sep 2026, relativa a la mediana del día de semana): correlación 0,49 el
mismo día y 0,35 con el día siguiente. Es señal para el seguimiento intradía y
para mañana; para siete días manda el clima.

QUÉ BAJA
--------
  ufs     Graficos/UFS/data/Datos_UFS.js — el total de usuarios sin servicio cada
          5 minutos, pero SÓLO de las últimas 24 horas. Lo que no se baja en el
          día se pierde: por eso corre cada hora.
  mapa    mapaCortes/datos/Datos_PaginaWeb.js — la foto del momento, con cada
          corte de media y baja tensión y sus usuarios afectados.
  github  github.com/arianacotrone/cortes-enre — un repositorio público que
          scrapea el mapa cada ~2 h desde el 2026-01-09. Es la única historia que
          hay; se carga una vez con --github. Su hora viene en UTC.
  tabla   paginacorte/js/data_EDS.js y data_EDN.js — la tabla de cortes de la
          página "Estado del servicio eléctrico": los usuarios sin luz SEPARADOS
          en programados, preventivos, media y baja tensión, y los programados por
          comunicado con su hora de inicio. Va a planificacion.CorteEnreTipo y
          CorteEnreComunicado (migración 2026-09-24c). Es sólo la foto del momento:
          por eso además corre cada 15 minutos con --solo-tabla.

SIN LA MIGRACIÓN
----------------
Si planificacion.CorteEnre no existe todavía (2026-09-14c), lo bajado se guarda
igual en logs/cortes_enre/*.jsonl y se vuelca solo en la primera corrida con la
tabla creada. Así la serie de 5 minutos no se pierde mientras la migración espera.

    python scripts/cortes_enre.py              # ufs + mapa + tabla (cron horario)
    python scripts/cortes_enre.py --solo-tabla # sólo la tabla por tipo (cron cada 15 min)
    python scripts/cortes_enre.py --github     # historia desde enero de 2026
"""

import argparse
import csv
import io
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import requests
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.database import engine                      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cortes_enre")

URL_UFS = "https://www.enre.gov.ar/Graficos/UFS/data/Datos_UFS.js"
URL_MAPA = "https://www.enre.gov.ar/mapaCortes/datos/Datos_PaginaWeb.js"
URL_GITHUB = "https://raw.githubusercontent.com/arianacotrone/cortes-enre/main/{}"
ARCHIVOS_GITHUB = ("cortes_enre.csv", "cortes_enre2.csv")
URL_TABLA = "https://www.enre.gov.ar/paginacorte/js/data_{}.js"
ARCHIVOS_TABLA = {"VOLTARA": "EDS", "NORLUZ": "EDN"}
# La página que la muestra; el ENRE registra de dónde viene cada lectura.
REFERER_TABLA = "https://www.argentina.gob.ar/enre/estado-del-servicio-electrico-de-voltara"
# Qué lista del archivo va a qué columna de CorteEnreTipo.
LISTAS_TABLA = {"cortesProgramados": "programados", "cortesPreventivos": "preventivos",
                "cortesServicioMedia": "media", "cortesServicioBaja": "baja",
                "cortesComunicados": "comunicados"}
# Una hora de actualización más vieja que esto se avisa en el log: el ENRE dejó
# de actualizar (la página tuvo un cartel de "falla informática" de Voltara).
MAX_ATRASO_TABLA = timedelta(hours=1)
BUFFER = REPO / "logs" / "cortes_enre"
# El servidor del ENRE contesta distinto sin un User-Agent de navegador.
CABECERAS = {"User-Agent": "Mozilla/5.0 (planificador Acme)"}
UTC_A_ARGENTINA = timedelta(hours=-3)

_FILA_UFS = re.compile(r"\['(\d{1,2}):(\d{2})',\s*(\d+),\s*(\d+)")
_RESUMEN = re.compile(r"<h3>(NORLUZ|VOLTARA)</h3><p>\s*(\d+)\s+usuarios", re.I)
_OBTENIDA = re.compile(r"(\d{1,2}) de (\w+) a las (\d{1,2}):(\d{2})", re.I)
_PUNTO = re.compile(r"CORTE DE (MEDIA|BAJA) TENSION</b>,\s*(VOLTARA|NORLUZ)[^\"]*?"
                    r"Usuarios afectados:\s*(\d+)", re.I)
_MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], start=1)}


def _get(url: str, referer: Optional[str] = None) -> str:
    cabeceras = {**CABECERAS, "Referer": referer} if referer else CABECERAS
    r = requests.get(url, headers=cabeceras, timeout=60)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


# ------------------------------------------------------------------ parseo

def parsear_ufs(contenido: str, ahora: datetime) -> list:
    """Las filas traen sólo HH:MM y cubren 24 horas que cruzan la medianoche.

    Se fechan de atrás para adelante desde la última (la más reciente, que no
    puede ser posterior a `ahora`): cada vez que la hora "sube" yendo hacia
    atrás, se cruzó la medianoche y se resta un día.
    """
    horas = [(int(h), int(m), int(eds), int(edn)) for h, m, eds, edn in _FILA_UFS.findall(contenido)]
    if not horas:
        return []
    salida = []
    dia = ahora.date()
    ultima = horas[-1]
    if (ultima[0], ultima[1]) > (ahora.hour, ahora.minute):
        dia -= timedelta(days=1)
    previo = None
    for h, m, eds, edn in reversed(horas):
        if previo is not None and (h, m) > previo:
            dia -= timedelta(days=1)
        previo = (h, m)
        momento = datetime(dia.year, dia.month, dia.day, h, m)
        salida.append({"momento": momento, "distribuidora": "VOLTARA", "fuente": "ufs", "usuarios": eds})
        salida.append({"momento": momento, "distribuidora": "NORLUZ", "fuente": "ufs", "usuarios": edn})
    return salida


def parsear_mapa(contenido: str, ahora: datetime) -> list:
    """Una fila por distribuidora con el total del resumen y el detalle por tensión."""
    m = _OBTENIDA.search(contenido)
    momento = ahora.replace(second=0, microsecond=0)
    if m and m.group(2).lower() in _MESES:
        dia, mes = int(m.group(1)), _MESES[m.group(2).lower()]
        anio = ahora.year - (1 if mes > ahora.month else 0)
        momento = datetime(anio, mes, dia, int(m.group(3)), int(m.group(4)))
    totales = {d.upper(): int(n) for d, n in _RESUMEN.findall(contenido)}
    detalle = {}
    for tension, dist, afectados in _PUNTO.findall(contenido):
        d = detalle.setdefault(dist.upper(), {"MEDIA": 0, "BAJA": 0, "cortes": 0})
        d[tension.upper()] += int(afectados)
        d["cortes"] += 1
    salida = []
    for dist in ("VOLTARA", "NORLUZ"):
        if dist not in totales and dist not in detalle:
            continue
        d = detalle.get(dist, {"MEDIA": 0, "BAJA": 0, "cortes": 0})
        salida.append({"momento": momento, "distribuidora": dist, "fuente": "mapa",
                       "usuarios": totales.get(dist, d["MEDIA"] + d["BAJA"]),
                       "media": d["MEDIA"], "baja": d["BAJA"], "cortes": d["cortes"]})
    return salida


def parsear_github(contenido: str) -> list:
    """Agrega el CSV por foto (fecha+hora de descarga) y distribuidora."""
    fotos = {}
    for fila in csv.DictReader(io.StringIO(contenido)):
        try:
            utc = datetime.strptime(f"{fila['fecha_descarga']} {fila['hora_descarga']}",
                                    "%Y-%m-%d %H:%M:%S")
            afectados = int(float(fila["afectados"] or 0))
        except (KeyError, ValueError):
            continue
        dist = (fila.get("empresa") or "").upper()
        if dist not in ("VOLTARA", "NORLUZ"):
            continue
        momento = (utc + UTC_A_ARGENTINA).replace(second=0)
        d = fotos.setdefault((momento, dist), {"media": 0, "baja": 0, "cortes": 0})
        d["media" if (fila.get("tipo") or "").lower().startswith("media") else "baja"] += afectados
        d["cortes"] += 1
    return [{"momento": m, "distribuidora": dist, "fuente": "github",
             "usuarios": d["media"] + d["baja"], "media": d["media"], "baja": d["baja"],
             "cortes": d["cortes"]} for (m, dist), d in sorted(fotos.items())]


def _cerrar(texto: str, desde: int, abre: str, cierra: str) -> int:
    """Posición del `cierra` que balancea el `abre` de `desde`, sin contar lo que
    está entre comillas simples (una calle puede traer corchetes)."""
    nivel, en_cadena, i = 0, False, desde
    while i < len(texto):
        c = texto[i]
        if en_cadena:
            if c == "\\":
                i += 1
            elif c == "'":
                en_cadena = False
        elif c == "'":
            en_cadena = True
        elif c == abre:
            nivel += 1
        elif c == cierra:
            nivel -= 1
            if nivel == 0:
                return i
        i += 1
    return -1


def _lista(contenido: str, clave: str) -> str:
    m = re.search(clave + r"\s*:\s*\[", contenido)
    if not m:
        return ""
    fin = _cerrar(contenido, m.end() - 1, "[", "]")
    return contenido[m.end():fin] if fin > 0 else ""


def _objetos(texto: str) -> list:
    """Los {clave: 'valor', ...} de una lista del archivo. No es JSON (claves sin
    comillas, valores con comillas simples), así que se lee a mano."""
    salida, i = [], 0
    while True:
        i = texto.find("{", i)
        if i < 0:
            return salida
        fin = _cerrar(texto, i, "{", "}")
        if fin < 0:
            return salida
        cuerpo = texto[i + 1:fin]
        salida.append({k: v for k, v in re.findall(r"(\w+)\s*:\s*'((?:[^'\\]|\\.)*)'", cuerpo)}
                      | {k: v for k, v in re.findall(r"(\w+)\s*:\s*(\[[^\]]*\])", cuerpo)})
        i = fin + 1


def _entero(valor) -> int:
    """'6.262' -> 6262. El punto es de miles."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    return int(digitos) if digitos else 0


def _fecha_hora(valor) -> Optional[datetime]:
    try:
        return datetime.strptime(str(valor).strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def parsear_tabla(contenido: str, distribuidora: str, ahora: datetime) -> Tuple[Optional[dict], list]:
    """(fila de CorteEnreTipo, comunicados) de un data_EDS.js / data_EDN.js.

    La hora de actualización viene sin fecha ('12:25'): es la de hoy, salvo que
    quede en el futuro, y entonces es la de ayer (una lectura pasada la
    medianoche de un archivo que se actualizó antes)."""
    m = re.search(r"ultimaActualizacion\s*:\s*'(\d{1,2}):(\d{2})'", contenido)
    if not m:
        return None, []
    momento = ahora.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if momento > ahora + timedelta(minutes=10):
        momento -= timedelta(days=1)

    def _escalar(clave):
        e = re.search(clave + r"\s*:\s*'([^']*)'", contenido)
        return _entero(e.group(1)) if e else None

    listas = {col: _objetos(_lista(contenido, clave)) for clave, col in LISTAS_TABLA.items()}
    suma = {col: sum(_entero(o.get("usuarios")) for o in objs) for col, objs in listas.items()}
    fila = {"momento": momento, "distribuidora": distribuidora,
            "sin_suministro": _escalar("totalUsuariosSinSuministro") or 0,
            "programados": suma["programados"], "preventivos": suma["preventivos"],
            "media": suma["media"], "baja": suma["baja"],
            "cortes_media": len(listas["media"]), "comunicados": suma["comunicados"],
            "ayer": _escalar("totalUsuariosAyer")}
    comunicados = []
    for o in listas["comunicados"]:
        inicio = _fecha_hora(o.get("fecha"))
        if inicio is None:
            continue
        comunicados.append({
            "distribuidora": distribuidora, "inicio": inicio,
            "alimentador": (o.get("subestacion_alimentador") or o.get("localidad") or "?")[:150],
            "partido": (o.get("partido") or None), "localidad": (o.get("localidad") or None),
            "usuarios": _entero(o.get("usuarios")) or None,
            "normalizacion": _fecha_hora(o.get("normalizacion")),
            "calles": (o.get("calles") or None), "visto": momento})
    return fila, comunicados


# ------------------------------------------------------------------ base

def hay_tabla(conn) -> bool:
    return conn.execute(text(
        "SELECT OBJECT_ID('planificacion.CorteEnre', 'U')")).scalar() is not None


_MERGE = text("""
    MERGE planificacion.CorteEnre AS t
    USING (SELECT :momento AS Momento, :distribuidora AS Distribuidora, :fuente AS Fuente) AS s
       ON t.Momento = s.Momento AND t.Distribuidora = s.Distribuidora AND t.Fuente = s.Fuente
    WHEN MATCHED THEN UPDATE SET UsuariosSinServicio = :usuarios, AfectadosMedia = :media,
                                 AfectadosBaja = :baja, Cortes = :cortes, CargadoEn = SYSDATETIME()
    WHEN NOT MATCHED THEN INSERT (Momento, Distribuidora, Fuente, UsuariosSinServicio,
                                  AfectadosMedia, AfectadosBaja, Cortes)
                          VALUES (:momento, :distribuidora, :fuente, :usuarios, :media, :baja, :cortes);
""")


def guardar(conn, filas: list) -> int:
    if not filas:
        return 0
    conn.execute(_MERGE, [{"media": None, "baja": None, "cortes": None, **f} for f in filas])
    return len(filas)


def hay_tablas_tipo(conn) -> bool:
    return conn.execute(text(
        "SELECT OBJECT_ID('planificacion.CorteEnreTipo', 'U')")).scalar() is not None


_MERGE_TIPO = text("""
    MERGE planificacion.CorteEnreTipo AS t
    USING (SELECT :momento AS Momento, :distribuidora AS Distribuidora) AS s
       ON t.Momento = s.Momento AND t.Distribuidora = s.Distribuidora
    WHEN MATCHED THEN UPDATE SET SinSuministro = :sin_suministro, Programados = :programados,
        Preventivos = :preventivos, MediaTension = :media, BajaTension = :baja,
        CortesMediaTension = :cortes_media, Comunicados = :comunicados, UsuariosAyer = :ayer,
        CargadoEn = SYSDATETIME()
    WHEN NOT MATCHED THEN INSERT (Momento, Distribuidora, SinSuministro, Programados, Preventivos,
        MediaTension, BajaTension, CortesMediaTension, Comunicados, UsuariosAyer)
        VALUES (:momento, :distribuidora, :sin_suministro, :programados, :preventivos, :media,
                :baja, :cortes_media, :comunicados, :ayer);
""")

_MERGE_COMUNICADO = text("""
    MERGE planificacion.CorteEnreComunicado AS t
    USING (SELECT :distribuidora AS Distribuidora, :inicio AS HoraProgramada,
                  :alimentador AS Alimentador) AS s
       ON t.Distribuidora = s.Distribuidora AND t.HoraProgramada = s.HoraProgramada
      AND t.Alimentador = s.Alimentador
    WHEN MATCHED THEN UPDATE SET Partido = :partido, Localidad = :localidad, Usuarios = :usuarios,
        Normalizacion = :normalizacion, Calles = :calles, VistoUltimo = :visto
    WHEN NOT MATCHED THEN INSERT (Distribuidora, HoraProgramada, Alimentador, Partido, Localidad,
        Usuarios, Normalizacion, Calles, VistoPrimero, VistoUltimo)
        VALUES (:distribuidora, :inicio, :alimentador, :partido, :localidad, :usuarios,
                :normalizacion, :calles, :visto, :visto);
""")


def bajar_tabla(ahora: datetime) -> Tuple[list, list, int]:
    """(filas por tipo, comunicados, errores) de las dos distribuidoras."""
    filas, comunicados, errores = [], [], 0
    for dist, sufijo in ARCHIVOS_TABLA.items():
        try:
            fila, com = parsear_tabla(_get(URL_TABLA.format(sufijo), REFERER_TABLA), dist, ahora)
        except requests.RequestException as e:
            errores += 1
            log.warning("ENRE: no se pudo bajar la tabla de %s: %s", dist, e)
            continue
        if fila is None:
            errores += 1
            log.warning("ENRE: la tabla de %s no trae hora de actualización (¿cambió el formato?)", dist)
            continue
        if ahora - fila["momento"] > MAX_ATRASO_TABLA:
            log.warning("ENRE: la tabla de %s no se actualiza desde las %s.", dist,
                        fila["momento"].strftime("%d/%m %H:%M"))
        partes = fila["programados"] + fila["preventivos"] + fila["media"] + fila["baja"]
        if fila["sin_suministro"] and abs(partes - fila["sin_suministro"]) > 0.02 * fila["sin_suministro"]:
            log.warning("ENRE: en %s los tipos suman %d y el total dice %d.", dist, partes,
                        fila["sin_suministro"])
        filas.append(fila)
        comunicados += com
    return filas, comunicados, errores


def guardar_tabla(conn, filas: list, comunicados: list) -> None:
    if filas:
        conn.execute(_MERGE_TIPO, filas)
    if comunicados:
        conn.execute(_MERGE_COMUNICADO, comunicados)


def _al_buffer(filas: list) -> Path:
    BUFFER.mkdir(parents=True, exist_ok=True)
    destino = BUFFER / f"{date.today():%Y-%m-%d}.jsonl"
    with destino.open("a", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps({**f, "momento": f["momento"].isoformat()}) + "\n")
    return destino


def _volcar_buffer(conn) -> int:
    total = 0
    for archivo in sorted(BUFFER.glob("*.jsonl")) if BUFFER.exists() else []:
        filas = []
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            f = json.loads(linea)
            f["momento"] = datetime.fromisoformat(f["momento"])
            filas.append(f)
        total += guardar(conn, filas)
        archivo.rename(archivo.with_suffix(".cargado"))
    return total


def _cargar_tabla(ahora: datetime) -> int:
    """La tabla por tipo va aparte del resto: su migración es otra (2026-09-24c) y
    que falle no tiene que impedir cargar el total de siempre."""
    filas, comunicados, errores = bajar_tabla(ahora)
    try:
        with engine.begin() as conn:
            if not hay_tablas_tipo(conn):
                log.warning("Falta la migración 2026-09-24c (planificacion.CorteEnreTipo): "
                            "la tabla por tipo no se guarda.")
                return 0
            guardar_tabla(conn, filas, comunicados)
    except Exception as e:  # noqa: BLE001 - el resto del script sigue
        log.error("ENRE: no se pudo guardar la tabla por tipo: %s", e)
        return 1
    for f in filas:
        log.info("ENRE %s %s: %d sin luz (programados %d, media %d, baja %d, preventivos %d); "
                 "%d comunicados.", f["distribuidora"], f["momento"].strftime("%H:%M"),
                 f["sin_suministro"], f["programados"], f["media"], f["baja"],
                 f["preventivos"], len([c for c in comunicados if c["distribuidora"] == f["distribuidora"]]))
    return 1 if errores == len(ARCHIVOS_TABLA) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--github", action="store_true",
                    help="carga la historia del repositorio público (una vez)")
    ap.add_argument("--solo-tabla", action="store_true",
                    help="sólo la tabla de cortes por tipo (cron cada 15 minutos)")
    args = ap.parse_args()

    ahora = datetime.now()
    if not args.github:
        codigo = _cargar_tabla(ahora)
        if args.solo_tabla:
            return codigo
    filas, errores = [], 0
    if args.github:
        for nombre in ARCHIVOS_GITHUB:
            filas += parsear_github(_get(URL_GITHUB.format(nombre)))
    else:
        for url, parser in ((URL_UFS, parsear_ufs), (URL_MAPA, parsear_mapa)):
            try:
                filas += parser(_get(url), ahora)
            except requests.RequestException as e:
                errores += 1
                log.warning("ENRE: no se pudo bajar %s: %s", url, e)
    log.info("ENRE: %d filas bajadas.", len(filas))

    with engine.begin() as conn:
        if not hay_tabla(conn):
            destino = _al_buffer(filas)
            log.warning("Falta la migración 2026-09-14c (planificacion.CorteEnre): lo "
                        "bajado queda en %s y se carga en la primera corrida con la tabla.",
                        destino)
            return 1 if errores == 2 else 0
        volcadas = _volcar_buffer(conn)
        n = guardar(conn, filas)
    log.info("ENRE: %d filas guardadas (%d desde el buffer).", n, volcadas)
    return 1 if errores == 2 else 0


if __name__ == "__main__":
    sys.exit(main())
