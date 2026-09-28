"""Historial y multi-chat del Asistente Analítico del Dashboard (Bandeja).

Persiste en `calidad.AsistenteConversaciones` + `calidad.AsistenteMensajes` los
hilos de conversación del asistente de /bandeja. Antes el historial vivía solo en
una variable de JavaScript: se perdía con cada recarga y no se podía tener más de
un análisis abierto.

Dos reglas que atraviesan todo el módulo:

1. **Cada conversación es privada de su dueño.** Todas las consultas filtran por
   `UsuarioId`; no existe un camino para leer el chat de otro. Por eso las
   funciones reciben siempre `user_id` y devuelven None/False en vez de levantar
   404 vs 403 (no confirmamos la existencia de un id ajeno).

2. **El chat guarda su alcance.** El asistente responde sobre el dataset filtrado
   en pantalla, así que un hilo sin la foto de sus filtros es un hilo que miente
   al reabrirlo. El JSON de `Alcance` viaja tal cual lo arma el frontend
   (empresa/campaña/plantilla, fechas, segmentadores y total de auditorías).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.types import NVARCHAR

from app.database import engine

logger = logging.getLogger(__name__)

MAX_TITULO = 200
# Turnos (user+bot) que se le mandan al modelo al continuar un hilo guardado.
# Coincide con asistente_dashboard.MAX_TURNOS_HISTORIAL.
MAX_TURNOS_MODELO = 10
# Techo del listado del panel; el buscador filtra server-side sobre este universo.
MAX_LISTADO = 100
_PREVIEW_CHARS = 160


def _titulo_limpio(texto: Optional[str], por_defecto: str = "Consulta sin título") -> str:
    limpio = " ".join((texto or "").split())
    return (limpio or por_defecto)[:MAX_TITULO]


def titulo_desde_pregunta(pregunta: str) -> str:
    """Título automático a partir de la primera pregunta del hilo."""
    limpio = " ".join((pregunta or "").split())
    if not limpio:
        return "Consulta sin título"
    if len(limpio) <= 70:
        return limpio
    # Cortamos en el último espacio para no partir una palabra al medio.
    recorte = limpio[:70]
    corte = recorte.rfind(" ")
    return (recorte[:corte] if corte > 40 else recorte).rstrip(" ,.;:") + "…"


def _escapar_like(patron: str) -> str:
    """Escapa los comodines de T-SQL. Los corchetes son una CLASE de caracteres:
    sin escaparlos, buscar '[algo' matchea cualquier texto con esas letras."""
    for char in ("\\", "%", "_", "["):
        patron = patron.replace(char, "\\" + char)
    return patron


def _fecha_iso(valor: Any) -> Optional[str]:
    """ISO con sufijo Z: las columnas son SYSUTCDATETIME y el navegador las
    muestra en hora local."""
    return valor.isoformat() + "Z" if valor else None


def _parse_alcance(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        cargado = json.loads(raw)
        return cargado if isinstance(cargado, dict) else None
    except (ValueError, TypeError):
        return None


def _dump_alcance(alcance: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(alcance, dict) or not alcance:
        return None
    try:
        return json.dumps(alcance, ensure_ascii=False)[:60_000]
    except (TypeError, ValueError):
        return None


def _entero(valor: Any) -> Optional[int]:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def listar(user_id: int, q: Optional[str] = None, limit: int = MAX_LISTADO) -> List[Dict[str, Any]]:
    """Chats vivos del usuario, del más reciente al más viejo.

    Con `q` filtra por título O por el contenido de cualquier mensaje del hilo
    (el buscador del panel: uno se acuerda de lo que preguntó, no de cómo quedó
    titulado el chat)."""
    limit = max(1, min(int(limit or MAX_LISTADO), MAX_LISTADO))
    params: Dict[str, Any] = {"uid": user_id, "limit": limit}
    filtro_q = ""
    termino = " ".join((q or "").split())
    if termino:
        params["q"] = f"%{_escapar_like(termino)}%"
        filtro_q = """
            AND (c.Titulo LIKE :q ESCAPE '\\'
                 OR EXISTS (SELECT 1 FROM calidad.AsistenteMensajes m
                            WHERE m.ConversacionId = c.Id AND m.Texto LIKE :q ESCAPE '\\'))
        """
    sql = text(f"""
        SELECT TOP (:limit)
               c.Id, c.Titulo, c.Alcance, c.PlantillaID, c.CampanaId,
               c.CreadoEn, c.ActualizadoEn,
               (SELECT COUNT(*) FROM calidad.AsistenteMensajes m WHERE m.ConversacionId = c.Id) AS Mensajes,
               (SELECT TOP 1 m.Texto FROM calidad.AsistenteMensajes m
                 WHERE m.ConversacionId = c.Id AND m.Rol = 'user'
                 ORDER BY m.Id DESC) AS UltimaPregunta
          FROM calidad.AsistenteConversaciones c
         WHERE c.UsuarioId = :uid AND c.Activo = 1 {filtro_q}
         ORDER BY c.ActualizadoEn DESC
    """)
    try:
        with engine.connect() as conn:
            filas = conn.execute(sql, params).mappings().all()
    except Exception as e:
        logger.exception("No se pudieron listar las conversaciones del asistente (uid=%s)", user_id)
        raise RuntimeError(str(e))

    return [
        {
            "id": f["Id"],
            "titulo": f["Titulo"],
            "alcance": _parse_alcance(f["Alcance"]),
            "plantilla_id": f["PlantillaID"],
            "campana_id": f["CampanaId"],
            "creado_en": _fecha_iso(f["CreadoEn"]),
            "actualizado_en": _fecha_iso(f["ActualizadoEn"]),
            "mensajes": f["Mensajes"] or 0,
            "preview": " ".join((f["UltimaPregunta"] or "").split())[:_PREVIEW_CHARS],
        }
        for f in filas
    ]


def obtener(user_id: int, conv_id: int) -> Optional[Dict[str, Any]]:
    """Un hilo completo con sus mensajes en orden, o None si no es del usuario."""
    try:
        with engine.connect() as conn:
            cab = conn.execute(
                text("""
                    SELECT Id, Titulo, Alcance, PlantillaID, CampanaId, CreadoEn, ActualizadoEn
                      FROM calidad.AsistenteConversaciones
                     WHERE Id = :id AND UsuarioId = :uid AND Activo = 1
                """),
                {"id": conv_id, "uid": user_id},
            ).mappings().first()
            if not cab:
                return None
            msgs = conn.execute(
                text("""
                    SELECT Rol, Texto, CreadoEn
                      FROM calidad.AsistenteMensajes
                     WHERE ConversacionId = :id
                     ORDER BY Id
                """),
                {"id": conv_id},
            ).mappings().all()
    except Exception as e:
        logger.exception("No se pudo leer la conversación %s (uid=%s)", conv_id, user_id)
        raise RuntimeError(str(e))

    return {
        "id": cab["Id"],
        "titulo": cab["Titulo"],
        "alcance": _parse_alcance(cab["Alcance"]),
        "plantilla_id": cab["PlantillaID"],
        "campana_id": cab["CampanaId"],
        "creado_en": _fecha_iso(cab["CreadoEn"]),
        "actualizado_en": _fecha_iso(cab["ActualizadoEn"]),
        "mensajes": [
            {"rol": m["Rol"], "texto": m["Texto"], "creado_en": _fecha_iso(m["CreadoEn"])}
            for m in msgs
        ],
    }


def historial_para_modelo(user_id: int, conv_id: int, max_turnos: int = MAX_TURNOS_MODELO) -> List[Dict[str, str]]:
    """Últimos turnos del hilo en el formato que espera `asistente_dashboard`.

    El historial se rearma SIEMPRE desde la base y no desde lo que manda el
    navegador: es la única forma de que continuar un chat reabierto en otra
    máquina vea lo mismo que se ve en pantalla."""
    tope = max(1, int(max_turnos)) * 2
    try:
        with engine.connect() as conn:
            filas = conn.execute(
                text("""
                    SELECT TOP (:tope) Rol, Texto
                      FROM calidad.AsistenteMensajes
                     WHERE ConversacionId = :id
                       AND EXISTS (SELECT 1 FROM calidad.AsistenteConversaciones c
                                    WHERE c.Id = :id AND c.UsuarioId = :uid)
                     ORDER BY Id DESC
                """),
                {"tope": tope, "id": conv_id, "uid": user_id},
            ).mappings().all()
    except Exception:
        logger.exception("No se pudo leer el historial de la conversación %s", conv_id)
        return []
    return [{"rol": f["Rol"], "texto": f["Texto"]} for f in reversed(filas)]


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

# El texto va con bind NVARCHAR explícito: las respuestas del asistente traen
# flechas, guiones largos y a veces emoji, y bindeados como VARCHAR el driver los
# convierte a la code page ANSI ANTES de insertar (se guardan como '?'). Migrar la
# columna a NVARCHAR no alcanza: el daño ocurre en el bind. Mismo problema que
# tuvo query_chatbots_logs.
_INSERT_MENSAJE = text("""
    INSERT INTO calidad.AsistenteMensajes (ConversacionId, Rol, Texto)
    VALUES (:id, :rol, :txt)
""").bindparams(bindparam("txt", type_=NVARCHAR))

_INSERT_CONVERSACION = text("""
    INSERT INTO calidad.AsistenteConversaciones
        (UsuarioId, Titulo, Alcance, PlantillaID, CampanaId)
    OUTPUT INSERTED.Id
    VALUES (:uid, :titulo, :alcance, :plantilla, :campana)
""").bindparams(
    bindparam("titulo", type_=NVARCHAR),
    bindparam("alcance", type_=NVARCHAR),
)


def crear(
    user_id: int,
    titulo: str,
    alcance: Optional[Dict[str, Any]] = None,
    plantilla_id: Any = None,
    campana_id: Any = None,
) -> int:
    """Crea un hilo vacío y devuelve su id."""
    try:
        with engine.begin() as conn:
            return conn.execute(
                _INSERT_CONVERSACION,
                {
                    "uid": user_id,
                    "titulo": _titulo_limpio(titulo),
                    "alcance": _dump_alcance(alcance),
                    "plantilla": _entero(plantilla_id),
                    "campana": _entero(campana_id),
                },
            ).scalar()
    except Exception as e:
        logger.exception("No se pudo crear la conversación del asistente (uid=%s)", user_id)
        raise RuntimeError(str(e))


def pertenece(user_id: int, conv_id: int) -> bool:
    try:
        with engine.connect() as conn:
            return bool(conn.execute(
                text("""
                    SELECT 1 FROM calidad.AsistenteConversaciones
                     WHERE Id = :id AND UsuarioId = :uid AND Activo = 1
                """),
                {"id": conv_id, "uid": user_id},
            ).scalar())
    except Exception:
        logger.exception("No se pudo validar la conversación %s (uid=%s)", conv_id, user_id)
        return False


def agregar_mensaje(conv_id: int, rol: str, texto: str) -> None:
    """Suma un turno y marca el hilo como recién usado.

    No falla hacia afuera: perder el guardado de un mensaje no puede tumbar la
    respuesta que el usuario ya está leyendo en pantalla."""
    if rol not in ("user", "bot") or not (texto or "").strip():
        return
    try:
        with engine.begin() as conn:
            conn.execute(_INSERT_MENSAJE, {"id": conv_id, "rol": rol, "txt": texto})
            conn.execute(
                text("""
                    UPDATE calidad.AsistenteConversaciones
                       SET ActualizadoEn = SYSUTCDATETIME()
                     WHERE Id = :id
                """),
                {"id": conv_id},
            )
    except Exception:
        logger.exception("No se pudo guardar el mensaje (%s) de la conversación %s", rol, conv_id)


def actualizar(
    user_id: int,
    conv_id: int,
    titulo: Optional[str] = None,
    alcance: Optional[Dict[str, Any]] = None,
) -> bool:
    """Renombra el hilo y/o refresca su alcance. Solo toca lo que viene."""
    sets: List[str] = []
    vals: Dict[str, Any] = {"id": conv_id, "uid": user_id}
    if titulo is not None:
        sets.append("Titulo = :titulo")
        vals["titulo"] = _titulo_limpio(titulo)
    if alcance is not None:
        sets.append("Alcance = :alcance")
        vals["alcance"] = _dump_alcance(alcance)
        sets.append("PlantillaID = :plantilla")
        vals["plantilla"] = _entero(alcance.get("plantilla_id"))
        sets.append("CampanaId = :campana")
        vals["campana"] = _entero(alcance.get("campana_id"))
    if not sets:
        return pertenece(user_id, conv_id)
    sentencia = text(f"""
        UPDATE calidad.AsistenteConversaciones
           SET {', '.join(sets)}
         WHERE Id = :id AND UsuarioId = :uid AND Activo = 1
    """).bindparams(*[
        bindparam(campo, type_=NVARCHAR) for campo in ("titulo", "alcance") if campo in vals
    ])
    try:
        with engine.begin() as conn:
            filas = conn.execute(sentencia, vals).rowcount
    except Exception as e:
        logger.exception("No se pudo actualizar la conversación %s", conv_id)
        raise RuntimeError(str(e))
    return bool(filas)


def eliminar(user_id: int, conv_id: int) -> bool:
    """Borrado suave: sale de la lista pero queda el rastro del consumo de IA."""
    try:
        with engine.begin() as conn:
            filas = conn.execute(
                text("""
                    UPDATE calidad.AsistenteConversaciones
                       SET Activo = 0, ActualizadoEn = SYSUTCDATETIME()
                     WHERE Id = :id AND UsuarioId = :uid AND Activo = 1
                """),
                {"id": conv_id, "uid": user_id},
            ).rowcount
    except Exception as e:
        logger.exception("No se pudo borrar la conversación %s", conv_id)
        raise RuntimeError(str(e))
    return bool(filas)
