"""Planificador: avisos de corte de Hidra -> ajuste propuesto al pronóstico.

POR QUÉ EXISTE
--------------
Los cortes programados son el 19% del error diario absoluto de Hidra Técnico, y
Hidra los anuncia uno o dos días antes en su web
(https://www.hidra.example/usuarios/Novedades): fecha y hora, estaciones
elevadoras que se paran y partidos o barrios afectados. Casi nunca dice cuántos
usuarios. Este módulo lee esos avisos (o un mail que se carga a mano), le pide a
Gemini los datos del corte, estima cuántas llamadas de más va a traer y deja un
AJUSTE PROPUESTO que alguien aplica o descarta desde la pantalla. No se aplica
solo: con la poca historia que hay, la estimación es una sugerencia.

CUÁNTO TRAE UN CORTE (medido, 2026)
-----------------------------------
Llamadas tipificadas 'Corte programado' por encima de la mediana de los 28 días
previos, sumando el día del corte y el siguiente:

    rutina (estación elevadora o inspección de río subterráneo, de 22 h a la
    tarde siguiente): 9, 23, 42, 43, 52, 54, 60, 72, 76, 137, 321 -> mediana 54
    grande: torre toma de Planta Belgrano (31/05, domingo entero) 470; válvulas
    de 700 mm en Caballito (13-14/08, CABA centro, sur y oeste) 1.644; Planta
    San Martín (03/05, domingo de día en CABA) 44 -> mediana 470

Un corte de rutina es +2-3% sobre un día de ~2.000 llamadas: casi ruido. Los
grandes son los que mueven el día (el 14/08 fue x1,63). Estos números son la
SEMILLA: el script mide cada corte después de que pasa (`medir_pendientes`)
y en cuanto hay MIN_MEDIDOS de una escala, manda la mediana medida.

Ojo con la medición: la tipificación también marca emergencias (el 04/07, la
rotura de Bernal, 697 llamadas) y dos avisos de la misma noche se atribuyen el
mismo exceso. Por eso se usa la mediana y no el promedio.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import planificador_datos as pdatos
from app.config import settings

logger = logging.getLogger(__name__)

TABLA = "planificacion.AvisoCorte"
URL_BASE = "https://www.hidra.example"
URL_NOVEDADES = URL_BASE + "/portal/usuarios/Novedades"
FEATURE_IA = "planificador_cortes"

# Qué campañas tienen avisos de corte y de dónde. Hoy sólo Hidra Técnico.
CAMPANAS_CON_AVISOS = {pdatos.CAMPANA_HIDRA_TECNICO}

ESCALAS = ("rutina", "grande")
# Llamadas de más por corte, hasta que haya historia medida propia (ver arriba).
LLAMADAS_SEMILLA = {"rutina": 54, "grande": 470}
MIN_MEDIDOS = 5
# Las llamadas siguen llegando después de que Hidra da el servicio por
# normalizado: la red tarda en recuperar presión.
COLA_DESPUES_DEL_FIN = timedelta(hours=4)
# Si el aviso no dice cuándo termina.
DURACION_POR_DEFECTO = timedelta(hours=16)
# Semanas para la línea de base de la ventana (mismo día de semana y hora).
SEMANAS_BASE_VENTANA = 4
# Un ajuste de este tamaño o más también carga el día como evento a excluir del
# entrenamiento: un día x1,5 no es un día normal y no tiene que enseñarle al
# perfil. Por debajo (la rutina, +2-3%) el día sigue siendo normal.
UMBRAL_EVENTO = 1.15
# Un aviso que ya empezó hace más que esto no propone ajuste (se guarda igual:
# sirve para medir).
TOLERANCIA_PASADO = timedelta(hours=2)
MAX_TEXTO = 12_000

_MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], start=1)}
_FECHA_NOTA = re.compile(r"(\d{1,2}) de (\w+) de (20\d\d)", re.I)
_ENLACE = re.compile(r'href="(/usuarios/Novedades/20\d\d/\d\d/[^"#?]+)"')


# ------------------------------------------------------------------ la web

def enlaces_de_novedades(pagina: str) -> List[str]:
    """Las URLs absolutas de las notas que lista la página de Novedades."""
    vistos: List[str] = []
    for ruta in _ENLACE.findall(pagina):
        url = URL_BASE + ruta
        if url not in vistos:
            vistos.append(url)
    return vistos


def _a_texto(fragmento: str) -> str:
    fragmento = re.sub(r"<(script|style)\b.*?</\1>", " ", fragmento, flags=re.S | re.I)
    fragmento = re.sub(r"<[^>]+>", " ", fragmento)
    return re.sub(r"\s+", " ", _html.unescape(fragmento)).strip()


def leer_nota(pagina: str) -> Tuple[Optional[date], Optional[str], str]:
    """(fecha de publicación, título, texto) de una nota de Novedades.

    La página repite el menú del sitio tres veces antes de la nota; el cuerpo
    arranca en la fecha ("16 de septiembre de 2026") y termina en el bloque de
    canales de atención del pie.
    """
    m = re.search(r"<title>(.*?)</title>", pagina, re.S | re.I)
    titulo = _a_texto(m.group(1)) if m else None
    if titulo and titulo.lower().startswith("hidra - "):
        titulo = titulo[len("hidra - "):]
    texto = _a_texto(pagina)
    publicado = None
    m = _FECHA_NOTA.search(texto)
    if m and m.group(2).lower() in _MESES:
        try:
            publicado = date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            publicado = None
        texto = texto[m.start():]
    texto = texto.split("Atención Comercial")[0].strip()
    return publicado, titulo, texto[:MAX_TEXTO]


# --------------------------------------------------------------- la lectura

SCHEMA_EXTRACCION = {
    "type": "OBJECT",
    "properties": {
        "es_corte": {"type": "BOOLEAN", "description":
                     "true si anuncia o informa una interrupción, baja presión o falta de agua que afecta a usuarios"},
        "programado": {"type": "BOOLEAN", "description":
                       "true si es un trabajo planificado; false si es una rotura, emergencia o falla"},
        "inicio": {"type": "STRING", "description":
                   "Inicio de la afectación, YYYY-MM-DDTHH:MM en hora argentina; vacío si no se sabe"},
        "fin": {"type": "STRING", "description":
                "Fin de la afectación, YYYY-MM-DDTHH:MM en hora argentina; vacío si no se sabe"},
        "instalaciones": {"type": "STRING", "description":
                          "Instalaciones intervenidas (estaciones elevadoras, plantas, tramos, cañerías)"},
        "zonas": {"type": "ARRAY", "items": {"type": "STRING"}, "description":
                  "Partidos, localidades y barrios afectados, tal como los nombra el aviso"},
        "afecta_caba": {"type": "BOOLEAN", "description":
                        "true si afecta barrios de la Ciudad de Buenos Aires"},
        "usuarios_afectados": {"type": "INTEGER", "nullable": True, "description":
                               "Cantidad de usuarios, cuentas o habitantes SOLO si el texto la da; si no, null"},
        "escala": {"type": "STRING", "enum": list(ESCALAS)},
        "resumen": {"type": "STRING", "description": "Una línea: qué se hace, cuándo y dónde"},
    },
    "required": ["es_corte", "programado", "inicio", "fin", "instalaciones", "zonas",
                 "afecta_caba", "usuarios_afectados", "escala", "resumen"],
}

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

PROMPT_EXTRACCION = """Sos analista de planificación de un call center que atiende la línea técnica de Hidra (agua potable y cloacas del AMBA). Te paso un aviso de Hidra: una nota de su web o un mail. Fue publicado el {publicado}{dia_semana}.

Extraé los datos del corte de servicio que anuncia.

REGLAS
- `es_corte`: true sólo si el aviso anuncia o informa una interrupción, baja presión o falta de agua que afecta (o afectó) a usuarios. Notas institucionales, balances, efemérides u obras sin afectación al servicio: false, y el resto de los campos vacíos.
- `programado`: true si es un trabajo planificado de antemano (mantenimiento, inspección, cambio de válvulas); false si es una rotura, emergencia o falla (también eléctrica).
- Fechas relativas ("este jueves", "mañana") se resuelven contra la fecha de publicación. "Por la noche" sin hora = 22:00. "Primeras horas de la mañana" = 08:00; "primeras horas de la tarde" o "hasta la tarde" = 15:00; "hasta horas de la noche" = 22:00; "durante la jornada" = 06:00 a 20:00 de ese día. Si el aviso habla de un corte que ya terminó, poné igual su inicio y su fin.
- `escala`: "grande" si para una planta potabilizadora o su toma de agua, si cambia válvulas o cañerías troncales de 700 mm o más dentro de la Ciudad, o si corta de día durante 6 horas o más en barrios de la Ciudad. Todo lo demás (una estación elevadora o una inspección de río subterráneo de noche, una cañería del conurbano) es "rutina".
- `usuarios_afectados`: sólo si el texto da un número. No lo estimes.
- No inventes: lo que el aviso no dice va vacío.

AVISO
{texto}
"""


_CLIENTE = None


def _cliente():
    """Uno por proceso y guardado: un genai.Client temporal se cierra al perder la
    referencia y la llamada sale con "the client has been closed"."""
    global _CLIENTE
    if _CLIENTE is None:
        from google import genai
        _CLIENTE = genai.Client(
            api_key=settings.GEMINI_CHATBOT_API_KEY or settings.GEMINI_AUDITORIA_API_KEY)
    return _CLIENTE


def _registrar_consumo(resp, modelo: str) -> None:
    """El consumo va al libro central de IA para que aparezca en /uso-ia."""
    try:
        from app.uso_ia import registrar_uso_ia
        usage = getattr(resp, "usage_metadata", None)
        registrar_uso_ia(feature=FEATURE_IA, modelo=modelo, modo="sync",
                         input_tokens=getattr(usage, "prompt_token_count", None),
                         output_tokens=getattr(usage, "candidates_token_count", None))
    except Exception:
        logger.debug("No se pudo registrar el consumo de la lectura del aviso.", exc_info=True)


def extraer_con_ia(texto: str, publicado: Optional[date]) -> Tuple[dict, str]:
    """(datos crudos del modelo, modelo usado). Levanta si el modelo falla: el
    que llama guarda el aviso en 'error' y lo reintenta en la próxima corrida."""
    from google.genai import types
    from AuditorIA import razonamiento

    modelo = settings.PLANIFICADOR_CORTES_MODELO
    prompt = PROMPT_EXTRACCION.format(
        publicado=publicado.isoformat() if publicado else "(fecha desconocida)",
        dia_semana=f" ({_DIAS[publicado.weekday()]})" if publicado else "",
        texto=texto[:MAX_TEXTO])
    resp = _cliente().models.generate_content(
        model=modelo, contents=prompt,
        config=types.GenerateContentConfig(
            # Resolver "este jueves" contra la fecha de publicación pide algo de
            # razonamiento; el resto es lectura.
            thinking_config=razonamiento.thinking_config("LOW", modelo=modelo,
                                                         include_thoughts=False),
            response_mime_type="application/json",
            response_schema=SCHEMA_EXTRACCION,  # type: ignore[arg-type]
            temperature=0.0,
        ))
    _registrar_consumo(resp, modelo)
    return json.loads(resp.text or "{}"), modelo


def _fecha_hora(valor) -> Optional[datetime]:
    if not valor or not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor.strip()[:16])
    except ValueError:
        return None


@dataclass
class Lectura:
    """Lo que se sacó del aviso, ya validado."""
    es_corte: bool
    programado: Optional[bool] = None
    inicio: Optional[datetime] = None
    fin: Optional[datetime] = None
    instalaciones: Optional[str] = None
    zonas: Optional[str] = None
    afecta_caba: Optional[bool] = None
    usuarios_afectados: Optional[int] = None
    escala: Optional[str] = None
    resumen: Optional[str] = None


def normalizar_lectura(crudo: dict) -> Lectura:
    """Valida lo que devolvió el modelo. Sin inicio no hay corte que planificar:
    se guarda como 'sin_corte' aunque el modelo haya dicho que sí."""
    if not crudo or not crudo.get("es_corte"):
        return Lectura(es_corte=False, resumen=(crudo or {}).get("resumen") or None)
    inicio = _fecha_hora(crudo.get("inicio"))
    fin = _fecha_hora(crudo.get("fin"))
    if inicio and fin and fin <= inicio:
        fin = None
    zonas = crudo.get("zonas") or []
    if isinstance(zonas, str):
        zonas = [zonas]
    usuarios = crudo.get("usuarios_afectados")
    escala = crudo.get("escala") if crudo.get("escala") in ESCALAS else "rutina"
    return Lectura(
        es_corte=inicio is not None,
        programado=bool(crudo.get("programado")),
        inicio=inicio, fin=fin,
        instalaciones=(crudo.get("instalaciones") or None),
        zonas=", ".join(str(z).strip() for z in zonas if str(z).strip())[:800] or None,
        afecta_caba=bool(crudo.get("afecta_caba")),
        usuarios_afectados=int(usuarios) if isinstance(usuarios, (int, float)) and usuarios > 0 else None,
        escala=escala,
        resumen=(crudo.get("resumen") or None),
    )


# --------------------------------------------------------------- la estimación

def ventana_del_ajuste(lectura: Lectura) -> Tuple[datetime, datetime]:
    """Desde el inicio del corte hasta unas horas después del fin: la gente sigue
    llamando mientras la red recupera presión."""
    fin = lectura.fin or (lectura.inicio + DURACION_POR_DEFECTO)
    return lectura.inicio, fin + COLA_DESPUES_DEL_FIN


def base_de_la_ventana(serie: Dict[Tuple[datetime, int], Tuple[float, float]],
                       desde: datetime, hasta: datetime, intervalo_min: int = 30,
                       semanas: int = SEMANAS_BASE_VENTANA) -> float:
    """Llamadas normales en la ventana: por cada media hora, la mediana del mismo
    día de semana y hora en las `semanas` anteriores (todas las colas sumadas).
    Una semana sin la media hora cuenta cero: la serie no trae los intervalos
    vacíos."""
    por_momento: Dict[datetime, float] = {}
    for (m, _), (ll, _) in serie.items():
        por_momento[m] = por_momento.get(m, 0.0) + ll
    paso = timedelta(minutes=intervalo_min)
    m = datetime.combine(desde.date(), time(desde.hour, (desde.minute // intervalo_min) * intervalo_min))
    total = 0.0
    while m < hasta:
        total += statistics.median(por_momento.get(m - timedelta(weeks=k), 0.0)
                                   for k in range(1, semanas + 1))
        m += paso
    return total


def llamadas_esperadas(escala: str, medidas: Dict[str, List[int]]) -> Tuple[int, str]:
    """(llamadas de más, de dónde sale). La mediana de lo medido de esa escala
    cuando ya hay MIN_MEDIDOS cortes; si no, la semilla."""
    propias = [x for x in medidas.get(escala, []) if x is not None]
    if len(propias) >= MIN_MEDIDOS:
        return int(round(statistics.median(propias))), f"mediana de {len(propias)} cortes medidos"
    return LLAMADAS_SEMILLA[escala], "valor semilla (2026)"


def factor_propuesto(extra: float, base: float) -> Optional[float]:
    """1 + extra/base, con tope en 3: más que eso en una ventana de un día no se
    vio nunca y seguramente es una base mal leída."""
    if base <= 0:
        return None
    return round(min(3.0, 1.0 + extra / base), 3)


# ------------------------------------------------------------------ la base

def hay_tabla(conn: Connection) -> bool:
    return pdatos._tiene_tabla(conn, TABLA)


def referencias_cargadas(conn: Connection, campana_id: int) -> set:
    """Las referencias ya guardadas, salvo las que fallaron: esas se reintentan."""
    filas = conn.execute(text(f"""
        SELECT Referencia FROM {TABLA}
        WHERE CampanaID = :c AND Estado <> 'error'
    """), {"c": campana_id}).scalars().all()
    return set(filas)


def llamadas_medidas(conn: Connection, campana_id: int) -> Dict[str, List[int]]:
    """Escala -> llamadas de más medidas en cortes PROGRAMADOS ya pasados."""
    filas = conn.execute(text(f"""
        SELECT Escala, LlamadasReales FROM {TABLA}
        WHERE CampanaID = :c AND EsCorte = 1 AND Programado = 1
          AND LlamadasReales IS NOT NULL AND Escala IS NOT NULL
    """), {"c": campana_id}).all()
    salida: Dict[str, List[int]] = {}
    for escala, n in filas:
        salida.setdefault(escala, []).append(int(n))
    return salida


def guardar_aviso(conn: Connection, campana_id: int, fuente: str, referencia: str,
                  publicado: Optional[date], titulo: Optional[str], texto: str,
                  lectura: Optional[Lectura], modelo: Optional[str], estado: str,
                  extra: Optional[int] = None, factor: Optional[float] = None,
                  ventana: Optional[Tuple[datetime, datetime]] = None) -> None:
    """Inserta el aviso, o pisa el que había quedado en 'error'."""
    lec = lectura or Lectura(es_corte=False)
    params = {
        "c": campana_id, "f": fuente, "r": referencia[:400],
        "p": datetime.combine(publicado, time(0)) if publicado else None,
        "t": (titulo or None) and titulo[:300], "tx": texto,
        "es": None if lectura is None else lec.es_corte,
        "pr": lec.programado, "i": lec.inicio, "fi": lec.fin,
        "ins": (lec.instalaciones or None) and lec.instalaciones[:400],
        "z": lec.zonas, "caba": lec.afecta_caba, "u": lec.usuarios_afectados,
        "esc": lec.escala, "res": (lec.resumen or None) and lec.resumen[:400],
        "m": modelo, "ll": extra, "fac": factor,
        "ad": ventana[0] if ventana else None, "ah": ventana[1] if ventana else None,
        "st": estado,
    }
    conn.execute(text(f"DELETE FROM {TABLA} WHERE CampanaID = :c AND Referencia = :r "
                      "AND Estado = 'error'"), params)
    conn.execute(text(f"""
        INSERT INTO {TABLA}
            (CampanaID, Fuente, Referencia, Publicado, Titulo, Texto, EsCorte, Programado,
             Inicio, Fin, Instalaciones, Zonas, AfectaCaba, UsuariosAfectados, Escala,
             Resumen, ModeloIA, LlamadasExtra, FactorPropuesto, AjusteDesde, AjusteHasta,
             Estado)
        VALUES (:c, :f, :r, :p, :t, :tx, :es, :pr, :i, :fi, :ins, :z, :caba, :u, :esc,
                :res, :m, :ll, :fac, :ad, :ah, :st)
    """), params)


def procesar_aviso(conn: Connection, campana_id: int, fuente: str, referencia: str,
                   publicado: Optional[date], titulo: Optional[str], texto: str,
                   ahora: Optional[datetime] = None, extraer=extraer_con_ia) -> str:
    """Lee un aviso, estima y lo guarda. Devuelve el estado con que quedó.

    `extraer` se inyecta para poder probarlo sin llamar a Gemini."""
    ahora = ahora or datetime.now()
    try:
        crudo, modelo = extraer(texto, publicado)
    except Exception as e:
        logger.warning("No se pudo leer el aviso %s: %s", referencia, e)
        guardar_aviso(conn, campana_id, fuente, referencia, publicado, titulo, texto,
                      None, None, "error")
        return "error"
    lectura = normalizar_lectura(crudo)
    if not lectura.es_corte:
        guardar_aviso(conn, campana_id, fuente, referencia, publicado, titulo, texto,
                      lectura, modelo, "sin_corte")
        return "sin_corte"

    ventana = ventana_del_ajuste(lectura)
    if lectura.inicio < ahora - TOLERANCIA_PASADO:
        # Ya pasó: no hay nada que planificar, pero sirve para medir.
        guardar_aviso(conn, campana_id, fuente, referencia, publicado, titulo, texto,
                      lectura, modelo, "vencido", ventana=ventana)
        return "vencido"

    extra, _ = llamadas_esperadas(lectura.escala, llamadas_medidas(conn, campana_id))
    serie = pdatos.serie_por_skill(
        conn, campana_id,
        (ventana[0] - timedelta(weeks=SEMANAS_BASE_VENTANA, days=1)).date(),
        ventana[1].date() + timedelta(days=1))
    base = base_de_la_ventana(serie, ventana[0], ventana[1])
    guardar_aviso(conn, campana_id, fuente, referencia, publicado, titulo, texto,
                  lectura, modelo, "pendiente", extra=extra,
                  factor=factor_propuesto(extra, base), ventana=ventana)
    return "pendiente"


def avisos(conn: Connection, campana_id: int, desde: date) -> List[dict]:
    """Los avisos con corte desde `desde` (por inicio del corte), más los que
    fallaron, para la pantalla. Sin el texto completo: pesa y no se muestra."""
    filas = conn.execute(text(f"""
        SELECT AvisoID, Fuente, Referencia, Publicado, Titulo, Programado, Inicio, Fin,
               Instalaciones, Zonas, AfectaCaba, UsuariosAfectados, Escala, Resumen,
               LlamadasExtra, FactorPropuesto, AjusteDesde, AjusteHasta, Estado,
               AjusteID, EventoID, LlamadasReales, RevisadoPor, RevisadoEn
        FROM {TABLA}
        WHERE CampanaID = :c
          AND ((EsCorte = 1 AND Inicio >= :d) OR Estado = 'error')
        ORDER BY Inicio DESC, AvisoID DESC
    """), {"c": campana_id, "d": desde}).mappings().all()
    return [dict(f) for f in filas]


def aplicar(conn: Connection, campana_id: int, aviso_id: int, usuario: Optional[int],
            factor: Optional[float] = None, desde: Optional[datetime] = None,
            hasta: Optional[datetime] = None) -> dict:
    """Crea el Ajuste (y el Evento si el día deja de ser normal) a partir de la
    propuesta, con lo que haya corregido la persona. Levanta ValueError si el
    aviso no existe o no está pendiente."""
    fila = conn.execute(text(f"""
        SELECT AvisoID, Estado, FactorPropuesto, AjusteDesde, AjusteHasta, Resumen,
               Titulo, Escala
        FROM {TABLA} WITH (UPDLOCK, ROWLOCK)
        WHERE AvisoID = :id AND CampanaID = :c
    """), {"id": aviso_id, "c": campana_id}).mappings().first()
    if fila is None:
        raise LookupError("Aviso inexistente.")
    if fila["Estado"] != "pendiente":
        raise ValueError(f"El aviso ya está {fila['Estado']}.")
    factor = float(factor if factor is not None else (fila["FactorPropuesto"] or 0))
    desde = desde or fila["AjusteDesde"]
    hasta = hasta or fila["AjusteHasta"]
    if not factor or factor <= 0 or factor > 10:
        raise ValueError("El factor va entre 0,01 y 10.")
    if not desde or not hasta or hasta <= desde:
        raise ValueError("La ventana del ajuste no es válida.")
    motivo = f"Corte Hidra: {fila['Resumen'] or fila['Titulo'] or 'aviso ' + str(aviso_id)}"[:400]
    ajuste_id = conn.execute(text("""
        INSERT INTO planificacion.Ajuste (CampanaID, SkillID, Desde, Hasta, Factor, Motivo, CreadoPor)
        OUTPUT INSERTED.AjusteID
        VALUES (:c, NULL, :d, :h, :f, :m, :u)
    """), {"c": campana_id, "d": desde, "h": hasta, "f": factor, "m": motivo,
           "u": usuario}).scalar()
    evento_id = None
    if factor >= UMBRAL_EVENTO:
        evento_id = conn.execute(text("""
            INSERT INTO planificacion.Evento
                (CampanaID, Desde, Hasta, Tipo, Descripcion, Factor, Origen,
                 ExcluirDeEntrenamiento, Confirmado, CreadoPor)
            OUTPUT INSERTED.EventoID
            VALUES (:c, :d, :h, 'corte', :m, :f, 'manual', 1, 1, :u)
        """), {"c": campana_id, "d": desde, "h": hasta, "f": factor, "m": motivo,
               "u": usuario}).scalar()
    conn.execute(text(f"""
        UPDATE {TABLA}
        SET Estado = 'aplicado', AjusteID = :a, EventoID = :e, RevisadoPor = :u,
            RevisadoEn = SYSUTCDATETIME()
        WHERE AvisoID = :id
    """), {"a": ajuste_id, "e": evento_id, "u": usuario, "id": aviso_id})
    return {"ajuste_id": int(ajuste_id), "evento_id": int(evento_id) if evento_id else None,
            "factor": factor}


def descartar(conn: Connection, campana_id: int, aviso_id: int,
              usuario: Optional[int]) -> None:
    filas = conn.execute(text(f"""
        UPDATE {TABLA}
        SET Estado = 'descartado', RevisadoPor = :u, RevisadoEn = SYSUTCDATETIME()
        WHERE AvisoID = :id AND CampanaID = :c AND Estado = 'pendiente'
    """), {"id": aviso_id, "c": campana_id, "u": usuario}).rowcount
    if not filas:
        raise LookupError("No hay un aviso pendiente con ese número.")


# ------------------------------------------------------------ lo que trajo

# Llamadas de Hidra Técnico tipificadas 'Corte programado', por día. Es la misma
# consulta con la que se midieron las semillas (sin filtrar el segmento).
LLAMADAS_CORTE_POR_DIA = """
    SELECT d.fecha_inicio AS dia, COUNT(*) AS llamadas
    FROM dbo.detalle_de_interacciones_por_agente d
    WHERE d.Inicio >= :desde AND d.Inicio < :hasta
      AND d.[Campaña] = 'HIDRAIN'
      AND d.[Tipificación] = 'Corte programado'
    GROUP BY d.fecha_inicio
"""
# La línea de base: los 28 días que terminan tres días antes del corte (el
# aviso sale uno o dos días antes y la gente ya llama).
DIAS_BASE_MEDICION = 28


def exceso_medido(por_dia: Dict[date, int], inicio: date, fin: date) -> int:
    """Llamadas por encima de la mediana previa, del día en que empieza el corte
    al día en que termina (igual que las semillas). Un día sin filas es cero, no
    falta de dato."""
    base = statistics.median(por_dia.get(inicio - timedelta(days=3 + i), 0)
                             for i in range(DIAS_BASE_MEDICION))
    total, d = 0.0, inicio
    while d <= fin:
        total += por_dia.get(d, 0) - base
        d += timedelta(days=1)
    return int(round(total))


def medir_pendientes(conn: Connection, campana_id: int, ahora: Optional[datetime] = None,
                     limite: int = 5) -> int:
    """Mide los cortes que terminaron hace más de dos días y todavía no tienen
    LlamadasReales. De a pocos por corrida: cada uno lee ~35 días del detalle
    por agente, que es una tabla productiva."""
    if campana_id != pdatos.CAMPANA_HIDRA_TECNICO:
        return 0
    ahora = ahora or datetime.now()
    filas = conn.execute(text(f"""
        SELECT TOP (:n) AvisoID, Inicio, COALESCE(Fin, Inicio) AS Fin
        FROM {TABLA}
        WHERE CampanaID = :c AND EsCorte = 1 AND LlamadasReales IS NULL
          AND Inicio IS NOT NULL AND COALESCE(Fin, Inicio) < :tope
        ORDER BY Inicio DESC
    """), {"n": limite, "c": campana_id, "tope": ahora - timedelta(days=2)}).mappings().all()
    for f in filas:
        inicio, fin = f["Inicio"].date(), f["Fin"].date()
        desde = inicio - timedelta(days=3 + DIAS_BASE_MEDICION)
        por_dia = {r["dia"]: int(r["llamadas"]) for r in conn.execute(
            text(LLAMADAS_CORTE_POR_DIA),
            {"desde": desde, "hasta": fin + timedelta(days=1)}).mappings()}
        conn.execute(text(f"""
            UPDATE {TABLA} SET LlamadasReales = :n, MedidoEn = SYSUTCDATETIME()
            WHERE AvisoID = :id
        """), {"n": exceso_medido(por_dia, inicio, fin), "id": f["AvisoID"]})
    return len(filas)
