import json
import logging

from sqlalchemy import bindparam, text

from app.dependencies import RoleChecker
from fastapi import APIRouter, HTTPException, Depends
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel
import pandas as pd
from sqlalchemy.exc import SQLAlchemyError
from app.models import (
    Plantilla, Atributos, Atributo, Skilldelete, User,
    MejorarPromptRequest, GenerarPlantillaRequest, DuplicarPlantillaRequest,
    RevisarPlantillaRequest, AplicarRevisionRequest, AtributoRevisado,
    RevisarAtributoRequest, GuardarVersionRequest, ConocimientoPlantillaRequest,
)

from app import revision_jobs
from app.managers import plantillas_manager_instance
from AuditorIA.Plantillas_prompts import plantillas_manager
from AuditorIA import asistente_plantillas
from AuditorIA import evidencia_plantilla
from AuditorIA.modelos_ia import catalogo_modelos_ia, MODELO_IA_DEFAULT
from AuditorIA import cache_plantillas
from AuditorIA import conocimiento_plantilla
from AuditorIA import gemini
from AuditorIA.execution_log import obtener_tarifa
from AuditorIA.razonamiento import catalogo_niveles_razonamiento, nivel_valido
from AuditorIA import limites_texto
from AuditorIA import senales_prompt
from AuditorIA import call_details
from AuditorIA import versionado
from app.config import settings
from app.database import engine
from app.rbac import exigir_acceso_empresa
from app.security import get_current_active_user

logger = logging.getLogger(__name__)
# Lecturas: accesibles con audit:execute O template:read (solo-ver plantillas).
# Las escrituras (POST/PUT/DELETE) re-exigen template:create por endpoint (ver abajo).
router = APIRouter(
    prefix="/Auditoria",
    dependencies=[Depends(RoleChecker(["audit:execute", "template:read"]))]
)

# Dependencia para operaciones de escritura (crear/editar/borrar plantillas,
# campañas, atributos y skills): template:create. Separado de audit:execute
# desde 2026-07-10: auditar no implica poder modificar el catálogo de plantillas.
require_template_create = RoleChecker(["template:create"])

# Permiso especial: ver/editar con qué audita una plantilla — el modelo de Gemini
# (histórico, ver AuditorIA/modelos_ia.py) y, desde 2026-08-19, el NIVEL DE
# RAZONAMIENTO, que es lo que se ofrece hoy en el editor (ver AuditorIA/razonamiento.py).
# Los dos van detrás del mismo permiso: revelan costos internos y mueven la factura.
# Deliberadamente separado de audit:execute/template:read: no todo el que edita
# plantillas debe poder ver/cambiar esto.
PERMISO_MODELO_IA = "template:modelo_ia"
require_modelo_ia = RoleChecker([PERMISO_MODELO_IA])


def _tiene_permiso_modelo_ia(current_user: User) -> bool:
    return current_user.is_super_admin or PERMISO_MODELO_IA in current_user.permissions


def _exigir_scope(user: User, **ids):
    """Alcance por empresa (permisos template:<empresa>): valida que los ids
    provistos (empresa_id/campana_id/plantilla_id/atributo_id) pertenezcan a
    empresas accesibles para el usuario. 403/404 si no."""
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, **ids)


if not plantillas_manager_instance:
    raise RuntimeError("Plantillas Manager no está inicializado correctamente.")

#* Endpoints
@router.get("/plantillas/ia/focos-revision", tags=["plantillas"])
def focos_revision_disponibles():
    """Aspectos que se le pueden pedir a la revisión integral que mire con lupa.

    El catálogo vive en el backend (misma fuente que usa el prompt del asistente) para
    que el editor no lo duplique.

    IMPORTANTE: va ANTES de "/plantillas/ia/{plantilla_id}" — si no, "focos-revision"
    matchea ahí y muere en la conversión a int (mismo caso que "/plantillas/modelos-ia",
    ver la nota de esa ruta).
    """
    return asistente_plantillas.FOCOS_REVISION

@router.get("/plantillas/ia/{plantilla_id}", tags=["plantillas"])
def obtener_plantilla_para_ia(plantilla_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        plantilla = plantillas_manager_instance.obtener_plantilla_para_IA(plantilla_id)
        return plantilla
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plantillas/modelos-ia", tags=["plantillas"], dependencies=[Depends(require_modelo_ia)])
def modelos_ia_disponibles():
    """Catálogo de modelos de Gemini seleccionables por plantilla (con precio y
    nivel de inteligencia). Gateado por el mismo permiso especial que protege
    el campo modelo_ia: quien no puede ver/editar el modelo tampoco necesita
    ver los precios internos.

    IMPORTANTE: esta ruta literal debe declararse ANTES de
    "/plantillas/{plantilla_id}" — FastAPI valida el tipo (int) del path param
    DESPUÉS de que Starlette ya hizo el match de ruta (el converter por
    defecto matchea cualquier string), así que si "/plantillas/{plantilla_id}"
    va primero, "modelos-ia" matchea ahí y falla la conversión a int (422)
    antes de siquiera llegar a esta ruta."""
    return catalogo_modelos_ia()

@router.get("/plantillas/niveles-razonamiento", tags=["plantillas"], dependencies=[Depends(require_modelo_ia)])
def niveles_razonamiento_disponibles():
    """Catálogo de niveles de razonamiento seleccionables por plantilla.

    Reemplazó al selector de modelo como perilla de costo/calidad: todas las
    plantillas auditan con el mismo modelo y lo que cambia es cuánto piensa Gemini
    antes de responder. Mismo permiso que el catálogo de modelos (mueve la factura).

    IMPORTANTE: ruta literal, va ANTES de "/plantillas/{plantilla_id}" por el mismo
    motivo que "/plantillas/modelos-ia" (ver arriba)."""
    return catalogo_niveles_razonamiento()

@router.get("/plantillas/limites-texto", tags=["plantillas"])
def limites_texto_atributos():
    """Tope de caracteres de los atributos de texto libre, para mostrarlo en el editor.

    El editor necesita el número para avisar ANTES de guardar (y para explicar por qué
    un atributo que pide la transcripción se rechaza). La fuente de verdad es la config
    del backend, así que se pide en vez de duplicarla en el JS.

    Va antes de "/plantillas/{plantilla_id}" por el mismo motivo que modelos-ia (ver nota
    de esa ruta)."""
    return {
        "max_caracteres": limites_texto.max_caracteres(),
        "bloquea_transcripcion": bool(getattr(settings, "ATRIBUTO_BLOQUEAR_TRANSCRIPCION", True)),
    }

def _senales_de_plantilla(plantilla_id: int) -> List[Dict[str, Any]]:
    """Señales vivas de una plantilla, para pintar el semáforo de cada atributo.

    Se recalculan sobre el estado guardado (no sobre lo que mandó el editor) para que lo
    que se ve sea lo que realmente quedó: si un guardado se forzó, la señal sigue ahí.
    """
    try:
        datos = plantillas_manager_instance.obtener_plantilla(plantilla_id)
    except RuntimeError:
        return []
    if not datos:
        return []
    atributos = asistente_plantillas.normalizar_atributos(datos.get("atributos"))
    # Con los campos del Call_details las señales dejan de suponer que la IA solo tiene
    # el audio (ver AuditorIA/call_details.py).
    return asistente_plantillas.senales_de_plantilla(
        atributos, campos_contexto=call_details.campos_de_plantilla(engine, plantilla_id))


def _etiqueta_usuario(user: User) -> str:
    """Cómo se identifica a quien saltea el gate en el log. Nombre + legajo: el legajo
    solo no se entiende leyendo el journal, y el nombre solo no es único."""
    return f"{user.Nombre or '?'} ({user.usuario})"


def _error_senales(exc: senales_prompt.SenalesAltasError) -> HTTPException:
    """400 estructurado: el editor necesita la lista para pintarla y ofrecer 'guardar igual'.

    `detail` va como dict y no como string porque un cartel de error suelto no le dice al
    analista qué atributo tocar ni cómo arreglarlo, que es todo el punto del gate.
    """
    return HTTPException(status_code=400, detail={
        "error": "senales_altas",
        "mensaje": "El atributo tiene problemas que conviene corregir antes de guardarlo.",
        "senales": exc.senales,
    })


@router.get("/plantillas/{plantilla_id}/senales", tags=["plantillas"])
def senales_de_plantilla(plantilla_id: int, current_user: User = Depends(get_current_active_user)):
    """Señales de UNA plantilla, agrupadas por atributo. Sin IA, sin tokens.

    Es lo que alimenta el semáforo del editor: el analista ve el problema mientras arma
    la plantilla, en el atributo que lo tiene, y no cuando alguien abre el chequeo de
    salud de la campaña tres semanas después.
    """
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    senales = _senales_de_plantilla(plantilla_id)
    por_atributo: Dict[str, List[Dict[str, Any]]] = {}
    generales: List[Dict[str, Any]] = []
    for senal in senales:
        if senal.get("atributo_id") is None:
            generales.append(senal)
        else:
            por_atributo.setdefault(str(senal["atributo_id"]), []).append(senal)
    altas = [s for s in senales if s["severidad"] == asistente_plantillas.SEVERIDAD_ALTA]
    return {
        "plantilla_id": plantilla_id,
        "senales": senales,
        "por_atributo": por_atributo,
        "generales": generales,
        "resumen": {"altas": len(altas), "medias": len(senales) - len(altas)},
    }


def _historial_por_version(plantilla_id: int, dias: int = 60) -> List[Dict[str, Any]]:
    """Tokens de las auditorías recientes de la plantilla, sumados por versión, con
    cuánto conocimiento de referencia leía cada versión.

    Es la materia prima del porcentaje de ahorro de la caché (ver
    `cache_plantillas.costo_por_auditoria`): sale de calidad.Auditorias y no del log de
    corridas porque hace falta saber con QUÉ versión se auditó cada llamado, y con eso
    qué bloque fijo pagó. Devuelve [] si la plantilla no auditó nada o si la consulta
    falla: ahí el aviso muestra el resto sin el porcentaje.
    """
    try:
        with engine.connect() as conn:
            filas = conn.execute(text("""
                SELECT PlantillaVersionID AS version_id,
                       COUNT(*) AS auditorias,
                       SUM(CAST(input_tokens AS BIGINT)) AS input_tokens,
                       SUM(CAST(COALESCE(output_tokens, 0) AS BIGINT)) AS output_tokens,
                       SUM(CAST(COALESCE(thoughts_tokens, 0) AS BIGINT)) AS thoughts_tokens
                FROM calidad.Auditorias
                WHERE PlantillaID = :pid AND input_tokens IS NOT NULL
                  AND FechaAuditoria >= DATEADD(day, -:dias, SYSDATETIME())
                GROUP BY PlantillaVersionID
            """), {"pid": plantilla_id, "dias": dias}).mappings().all()
            historial = [dict(f) for f in filas]

            versiones = [int(h["version_id"]) for h in historial if h["version_id"] is not None]
            snapshots = {}
            if versiones:
                snapshots = {
                    int(v): s for v, s in conn.execute(text("""
                        SELECT VersionID, SnapshotJSON FROM calidad.PlantillaVersiones
                        WHERE VersionID IN :ids
                    """).bindparams(bindparam("ids", expanding=True)), {"ids": versiones}).all()
                }
    except Exception as e:
        logger.warning("No se pudo leer el historial de tokens de la plantilla %s: %s",
                       plantilla_id, e)
        return []

    # El conocimiento de cada versión se mide con el texto de HOY de sus documentos:
    # el de entonces no se guarda (el snapshot tiene el hash, no el contenido) y una
    # edición mueve poco el tamaño. Una versión sin la clave no leía documentos.
    tokens_por_seleccion: Dict[tuple, int] = {}
    for h in historial:
        h["tokens_conocimiento"] = 0
        try:
            snapshot = json.loads(snapshots.get(h["version_id"]) or "{}")
        except (TypeError, ValueError):
            continue
        seleccion = tuple(sorted(int(d["doc_id"]) for d in (snapshot.get("conocimiento") or [])))
        if not seleccion:
            continue
        if seleccion not in tokens_por_seleccion:
            docs = conocimiento_plantilla.documentos_por_id(engine, seleccion, solo_activos=False)
            tokens_por_seleccion[seleccion] = conocimiento_plantilla.estimar_tokens(
                len(conocimiento_plantilla.bloque_para_el_prompt(docs)))
        h["tokens_conocimiento"] = tokens_por_seleccion[seleccion]
    return historial


@router.get("/plantillas/{plantilla_id}/cache-contexto", tags=["plantillas"])
def cache_contexto_plantilla(
    plantilla_id: int,
    modo: str = "batch",
    current_user: User = Depends(get_current_active_user),
):
    """Cuánto ahorra la caché de contexto en una corrida de esta plantilla.

    Lo consume el aviso de la pantalla de Auditar, que necesita contestar dos cosas
    mientras el usuario arma la corrida: cuánto pesa el bloque fijo de la plantilla
    (lo que hoy se paga en CADA auditoría) y a partir de cuántas auditorías la caché
    se paga sola. Ver AuditorIA/cache_plantillas.py.

    Barato a propósito: lee la plantilla y la tarifa, estima los tokens por caracteres
    y no toca Gemini. Se llama cada vez que se cambia la plantilla en el combo.
    """
    try:
        prompt_info = gemini.prompt(plantilla_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    modelo = prompt_info.get("modelo") or MODELO_IA_DEFAULT
    modo = modo if modo in ("sync", "batch", "flex") else "batch"
    tarifa = obtener_tarifa(engine, modelo=modelo)
    datos = cache_plantillas.estimar_ahorro(
        gemini.instruccion_de_sistema(prompt_info),
        prompt_info.get("text") or "",
        tarifa=tarifa,
        modo=modo,
    )
    datos["modelo"] = modelo
    # Cuánto del bloque fijo son los documentos de referencia: el aviso lo aclara,
    # porque es lo que hace que una plantilla pese diez veces más que otra.
    tokens_conocimiento = conocimiento_plantilla.estimar_tokens(len(prompt_info.get("conocimiento") or ""))
    datos["tokens_conocimiento"] = tokens_conocimiento
    # Para poder decir "un 18% más barata" en vez de "US$0,26": el porcentaje es lo
    # que se entiende cuando el monto por corrida es de centavos. El denominador es lo
    # que cuesta una auditoría con el bloque de HOY (ver cache_plantillas.costo_por_auditoria).
    datos["costo_usd_por_auditoria"] = cache_plantillas.costo_por_auditoria(
        _historial_por_version(plantilla_id),
        tokens_conocimiento_actual=tokens_conocimiento,
        tarifa=tarifa,
        modo=modo,
    )
    return datos


@router.get("/conocimiento/catalogo", tags=["plantillas"])
def catalogo_conocimiento(current_user: User = Depends(get_current_active_user)):
    """Los documentos de los chatbots que se pueden elegir como conocimiento de
    referencia de una plantilla, agrupados por bot (ver
    AuditorIA/conocimiento_plantilla.py). Solo títulos y tamaños, no el contenido."""
    try:
        return conocimiento_plantilla.catalogo(engine)
    except SQLAlchemyError as e:
        logger.exception("No se pudo leer el catálogo de conocimiento")
        raise HTTPException(status_code=500, detail=f"No se pudo leer el catálogo: {e}")


@router.get("/plantillas/{plantilla_id}/conocimiento", tags=["plantillas"])
def conocimiento_de_plantilla(plantilla_id: int, current_user: User = Depends(get_current_active_user)):
    """Qué documentos lee la IA al auditar con esta plantilla y cuánto pesan.

    `disponible` en False = falta la migración 2026-09-21c: el editor lo muestra y
    no ofrece elegir."""
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        return conocimiento_plantilla.resumen_de_plantilla(engine, plantilla_id)
    except SQLAlchemyError as e:
        logger.exception("No se pudo leer el conocimiento de la plantilla %s", plantilla_id)
        raise HTTPException(status_code=500, detail=f"No se pudo leer el conocimiento: {e}")


@router.put("/plantillas/{plantilla_id}/conocimiento", tags=["plantillas"],
            dependencies=[Depends(require_template_create)])
def guardar_conocimiento_de_plantilla(plantilla_id: int, payload: ConocimientoPlantillaRequest,
                                      current_user: User = Depends(get_current_active_user)):
    """Reemplaza los documentos de referencia de la plantilla. Rige desde la próxima
    corrida y, como cambia lo que la IA lee, abre una versión nueva de la plantilla
    cuando audite (ver AuditorIA/versionado.py)."""
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        conocimiento_plantilla.guardar_seleccion(
            engine, plantilla_id, payload.doc_ids, user_id=current_user.usuario,
        )
    except conocimiento_plantilla.ConocimientoNoDisponible as e:
        raise HTTPException(status_code=409, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SQLAlchemyError as e:
        logger.exception("No se pudo guardar el conocimiento de la plantilla %s", plantilla_id)
        raise HTTPException(status_code=500, detail=f"No se pudo guardar: {e}")
    return conocimiento_plantilla.resumen_de_plantilla(engine, plantilla_id)


@router.get("/plantillas/salud/{campana_id}", tags=["plantillas"])
def salud_plantillas_campana(campana_id: int, current_user: User = Depends(get_current_active_user)):
    """Chequeo estructural de TODAS las plantillas de una campaña. Sin IA, sin tokens.

    Las mismas señales que la revisión le pasa masticadas al modelo
    (`asistente_plantillas.senales_de_plantilla`) sirven solas como diagnóstico: un enum
    sin salida segura, un atributo de calidad con peso 0 o dos atributos con el mismo
    nombre son problemas verificables que no dependen del criterio de nadie. Correrlas
    sobre la campaña entera contesta "¿cuál de mis 12 plantillas está rota?" sin gastar
    un token y sin tocar las auditorías.

    Deliberadamente NO incluye las señales que salen de los datos de uso (esas son una
    consulta agregada por plantilla, ver AuditorIA/evidencia_plantilla.py): esto tiene
    que ser barato para poder mirarlo seguido.

    Ruta literal en el segundo segmento, como "/plantillas/listar/{campana_id}".
    """
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        plantillas = plantillas_manager_instance.listar_plantillas(campana_id) or {}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Todas las plantillas de la campaña comparten empresa, así que comparten los campos
    # del Call_details: se resuelven UNA vez y no por plantilla.
    campos = call_details.campos_de_campana(engine, campana_id)

    revisadas: List[Dict[str, Any]] = []
    for plantilla_id, nombre in plantillas.items():
        try:
            datos = plantillas_manager_instance.obtener_plantilla(int(plantilla_id))
        except RuntimeError:
            datos = None
        if not datos:
            continue
        atributos = asistente_plantillas.normalizar_atributos(datos.get("atributos"))
        senales = asistente_plantillas.senales_de_plantilla(atributos, campos_contexto=campos)
        altas = [s for s in senales if s["severidad"] == asistente_plantillas.SEVERIDAD_ALTA]
        revisadas.append({
            "plantilla_id": int(plantilla_id),
            "nombre": nombre,
            "atributos": len(atributos),
            "sin_atributos": len(atributos) == 0,
            "senales": senales,
            "altas": len(altas),
            "medias": len(senales) - len(altas),
            "estado": "alta" if altas else ("media" if senales else "ok"),
        })

    # Peor primero: la lista se lee de arriba hacia abajo y lo que hay que arreglar
    # tiene que estar arriba.
    orden = {"alta": 0, "media": 1, "ok": 2}
    revisadas.sort(key=lambda p: (orden[p["estado"]], -p["altas"], -p["medias"], p["nombre"] or ""))
    return {
        "campana_id": campana_id,
        "plantillas": revisadas,
        "resumen": {
            "total": len(revisadas),
            "con_problemas": sum(1 for p in revisadas if p["estado"] != "ok"),
            "criticas": sum(1 for p in revisadas if p["estado"] == "alta"),
        },
    }

@router.get("/plantillas/{plantilla_id}", tags=["plantillas"])
def obtener_plantilla(plantilla_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        plantilla = plantillas_manager_instance.obtener_plantilla(plantilla_id)
        if plantilla is None:
            raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        if not _tiene_permiso_modelo_ia(current_user):
            plantilla.pop("modelo_ia", None)
            plantilla.pop("nivel_razonamiento", None)
        return plantilla
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/empresas", tags=["plantillas"])
def empresas_disponibles(current_user: User = Depends(get_current_active_user)):
    try:
        empresas = plantillas_manager_instance.empresas_disponibles(
            user_permissions=current_user.permissions,
            is_super_admin=current_user.is_super_admin
        )
        return empresas
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/campanas/{empresa_id}", tags=["plantillas"])
def campanas_disponibles(empresa_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, empresa_id=empresa_id)
    try:
        campanas = plantillas_manager_instance.campanas_disponibles(empresa_id)
        return campanas
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plataformas", tags=["plantillas"])
def plataformas_disponibles():
    try:
        plataformas = plantillas_manager_instance.plataformas_disponibles()
        return plataformas
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/skills/{empresa_id}", tags=["plantillas"])
def skills_disponibles(empresa_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, empresa_id=empresa_id)
    try:
        skills = plantillas_manager_instance.skill_disponibles(empresa_id)
        return skills
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/skills/asignados/{campana_id}", tags=["plantillas"])
def skill_asignados(campana_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        skills = plantillas_manager_instance.skill_asignados(campana_id)
        return skills
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plantillas/uso/{campana_id}", tags=["plantillas"])
def uso_plantillas_campana(campana_id: int, current_user: User = Depends(get_current_active_user)):
    """Devuelve estadísticas de uso de las plantillas de una campaña (conteo en últimos meses
    y tiempo transcurrido desde la última auditoría) con semáforo de colores.
    """
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        plantillas = plantillas_manager_instance.obtener_uso_plantillas(campana_id)
        total = len(plantillas)
        activas = sum(1 for p in plantillas if p["estado_uso"] == "activa")
        inactivas_1m = sum(1 for p in plantillas if p["estado_uso"] == "inactiva_1m")
        inactivas_3m = sum(1 for p in plantillas if p["estado_uso"] == "inactiva_3m")
        sin_uso = sum(1 for p in plantillas if p["estado_uso"] == "sin_uso")

        return {
            "campana_id": campana_id,
            "plantillas": plantillas,
            "resumen": {
                "total": total,
                "activas": activas,
                "inactivas_1m": inactivas_1m,
                "inactivas_3m": inactivas_3m,
                "sin_uso": sin_uso,
                "candidatas_eliminar": inactivas_3m + sin_uso,
            },
        }
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plantillas/listar/{campana_id}", tags=["plantillas"])
def listar_plantillas(campana_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        plantillas = plantillas_manager_instance.listar_plantillas(campana_id)
        return plantillas
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tipificaciones/{campana_id}", tags=["plantillas"])
def listar_tipificaciones(campana_id: int, current_user: User = Depends(get_current_active_user)) -> Union[List[str], List[Dict[str, Any]]]: # Permitir lista de strings o lista de dicts (árbol)
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        tipificaciones = plantillas_manager_instance.listar_tipificaciones(campana_id)
        return tipificaciones
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class FiltrosResumenRequest(BaseModel):
    fecha_desde: Optional[str] = None
    fecha_hasta: Optional[str] = None
    duracion_min: Optional[int] = None
    duracion_max: Optional[int] = None
    sentido: Optional[List[str]] = None
    loginid: Optional[List[str]] = None
    tipificacion: Optional[List[str]] = None
    reauditar: Optional[bool] = False
    comentario: Optional[List[str]] = None


@router.post("/filtros-resumen/{campana_id}", tags=["AuditorIA"])
def filtros_resumen(
    campana_id: int,
    req: FiltrosResumenRequest,
    current_user: User = Depends(get_current_active_user)
) -> Dict[str, Any]:
    """Devuelve totales de audios, tipificaciones con conteo y operadores activos."""
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        with engine.connect() as conn:
            from app.utils.filtros_resumen import obtener_filtros_resumen
            return obtener_filtros_resumen(conn, campana_id, req.dict())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error al calcular filtros-resumen para campana {campana_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error al consultar métricas de la campaña: {str(e)}")

@router.post("/campanas", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def crear_campana(nombre: str, Empresa_id: int, plataforma_id: int, current_user: User = Depends(get_current_active_user)) -> int:
    _exigir_scope(current_user, empresa_id=Empresa_id)
    try:
        campana_id = plantillas_manager_instance.crear_campaña(nombre, Empresa_id, plataforma_id)
        return campana_id
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/plantillas", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def crear_plantilla(plantilla: Plantilla, current_user: User = Depends(get_current_active_user)):
    if plantilla.modelo_ia is not None and not _tiene_permiso_modelo_ia(current_user):
        raise HTTPException(status_code=403, detail="No tenés permiso para configurar el modelo de IA de la plantilla.")
    if plantilla.nivel_razonamiento is not None:
        if not _tiene_permiso_modelo_ia(current_user):
            raise HTTPException(status_code=403, detail="No tenés permiso para configurar el nivel de razonamiento de la plantilla.")
        # Se valida acá y no solo en el CHECK de la BD para devolver un 400 con
        # mensaje en vez de un 500 con el error crudo de SQL Server.
        if not nivel_valido(plantilla.nivel_razonamiento):
            raise HTTPException(status_code=400, detail=f"Nivel de razonamiento desconocido: {plantilla.nivel_razonamiento}")
    if plantilla.campanas_id:
        _exigir_scope(current_user, campana_id=plantilla.campanas_id)
    try:
        plantilla_id = plantillas_manager_instance.crear_plantilla(
            nombre=plantilla.nombre,
            descripcion=plantilla.descripcion,
            system_prompt=plantilla.system_prompt,
            recordatorio=plantilla.recordatorio,
            campanas_id=plantilla.campanas_id or 0,
            modelo_ia=plantilla.modelo_ia,
            nivel_razonamiento=plantilla.nivel_razonamiento,
        )
        return {"plantilla_id": plantilla_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/plantillas/{plantilla_id}/duplicar", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def duplicar_plantilla(plantilla_id: int, payload: Optional[DuplicarPlantillaRequest] = None,
                       current_user: User = Depends(get_current_active_user)):
    """Copia una plantilla (cabecera + atributos) dentro de la misma campaña o
    en otra. Sin body, la copia se llama '<nombre> (copia)' y queda en la campaña
    del original. Se valida el alcance por empresa tanto de la plantilla origen
    como de la campaña destino: duplicar no puede usarse para meter una plantilla
    en una empresa a la que el usuario no tiene acceso."""
    payload = payload or DuplicarPlantillaRequest()
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    if payload.campana_id:
        _exigir_scope(current_user, campana_id=payload.campana_id)
    try:
        return plantillas_manager_instance.duplicar_plantilla(
            plantilla_id=plantilla_id,
            nuevo_nombre=payload.nombre,
            campana_id=payload.campana_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/plantillas/{plantilla_id}/atributos", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def crear_atributos_plantilla(plantilla_id: int, atributos: Atributos, forzar: bool = False,
                              current_user: User = Depends(get_current_active_user)):
    """Alta de atributos. `forzar=true` guarda salteando el gate de señales altas.

    El editor lo manda solo después de mostrarle al usuario qué está salteando (el 400
    trae la lista); el salteo queda logueado y el problema sigue apareciendo en el
    semáforo y en el reporte de salud, así que forzar no lo esconde.
    """
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        plantillas_manager_instance.crear_atributos_plantilla(
            plantilla_id, [dict(attr) for attr in atributos.atributos],
            forzar=forzar, usuario=_etiqueta_usuario(current_user),
        )
        return {"status": "success", "senales": _senales_de_plantilla(plantilla_id)}
    except senales_prompt.SenalesAltasError as e:
        raise _error_senales(e)
    except ValueError as e:
        # Atributo rechazado por los límites de texto libre (AuditorIA/limites_texto.py):
        # el mensaje es para el usuario, así que viaja tal cual al editor.
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/campanas/{campana_id}", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def desactivar_campana(campana_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        plantillas_manager_instance.desactivar_campana(campana_id)
        return {"status": "success"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/plantillas/{plantilla_id}", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def desactivar_plantilla(plantilla_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        plantillas_manager_instance.desactivar_plantilla(plantilla_id)
        return {"status": "success"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/atributos/{atributo_id}", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def desactivar_atributo(atributo_id: int, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, atributo_id=atributo_id)
    try:
        plantillas_manager_instance.desactivar_atributo(atributo_id)
        return {"status": "success"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/skills/eliminar", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def desactivar_skill(data: Skilldelete, current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, campana_id=data.campana_id)
    try:
        plantillas_manager_instance.desactivar_skill(data.campana_id, data.skills)
        return {"status": "success"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/plantillas/{plantilla_id}", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def modificar_plantilla(plantilla_id: int, plantilla: Plantilla, current_user: User = Depends(get_current_active_user)):
    if plantilla.modelo_ia is not None and not _tiene_permiso_modelo_ia(current_user):
        raise HTTPException(status_code=403, detail="No tenés permiso para configurar el modelo de IA de la plantilla.")
    if plantilla.nivel_razonamiento is not None:
        if not _tiene_permiso_modelo_ia(current_user):
            raise HTTPException(status_code=403, detail="No tenés permiso para configurar el nivel de razonamiento de la plantilla.")
        # Se valida acá y no solo en el CHECK de la BD para devolver un 400 con
        # mensaje en vez de un 500 con el error crudo de SQL Server.
        if not nivel_valido(plantilla.nivel_razonamiento):
            raise HTTPException(status_code=400, detail=f"Nivel de razonamiento desconocido: {plantilla.nivel_razonamiento}")
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    try:
        plantillas_manager_instance.modificar_plantilla(
            plantilla_id=plantilla_id,
            nombre=plantilla.nombre,
            descripcion=plantilla.descripcion,
            system_prompt=plantilla.system_prompt,
            recordatorio=plantilla.recordatorio,
            modelo_ia=plantilla.modelo_ia,
            nivel_razonamiento=plantilla.nivel_razonamiento,
        )
        return {"status": "success"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/atributos/{atributo_id}", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def modificar_atributo(atributo_id: int, atributo: Atributo, forzar: bool = False,
                       current_user: User = Depends(get_current_active_user)):
    """Edición de un atributo. `forzar=true` saltea el gate de señales altas (ver el alta).

    El gate solo frena lo que ESTA edición introduce: un atributo que ya venía con
    problemas se puede seguir editando y reordenando sin tener que arreglarlo primero.
    """
    _exigir_scope(current_user, atributo_id=atributo_id)
    try:
        plantillas_manager_instance.modificar_atributo(
            atributo_id=atributo_id,
            nombre=atributo.nombre,
            prompt=atributo.prompt,
            tipo=atributo.tipo,
            restricciones=atributo.restricciones,
            orden=atributo.orden,
            DarAviso=atributo.DarAviso,       # <-- NUEVO
            FrasesAviso=atributo.FrasesAviso, # <-- NUEVO
            ponderacion=atributo.ponderacion, # <-- NUEVO (peso del atributo)
            es_opcional=atributo.es_opcional, # <-- NUEVO (la IA puede dejarlo sin responder)
            forzar=forzar, usuario=_etiqueta_usuario(current_user),
        )
        return {"status": "success"}
    except senales_prompt.SenalesAltasError as e:
        raise _error_senales(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

#* Asistente de IA para plantillas (mejorar / generar prompts)
# Ambos endpoints SOLO devuelven una propuesta: no modifican nada en la BD.
# El guardado lo hace el usuario al aceptar, con los endpoints existentes
# (PUT plantillas/atributos, POST plantillas + atributos).
@router.post("/plantillas/ia/mejorar-prompt", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def ia_mejorar_prompt(payload: MejorarPromptRequest, current_user: User = Depends(get_current_active_user)):
    if payload.plantilla_id:
        _exigir_scope(current_user, plantilla_id=payload.plantilla_id)
    try:
        return asistente_plantillas.mejorar_prompt(
            campo=payload.campo,
            texto_actual=payload.texto_actual,
            instruccion_usuario=payload.instruccion_usuario,
            contexto=payload.contexto,
            user_id=current_user.usuario,
            campos_contexto=call_details.campos_de_plantilla(engine, payload.plantilla_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Falla del modelo / API de Gemini.
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Error inesperado al mejorar el prompt: {e}")

@router.post("/plantillas/ia/generar-plantilla", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def ia_generar_plantilla(payload: GenerarPlantillaRequest, current_user: User = Depends(get_current_active_user)):
    if payload.campana_id:
        _exigir_scope(current_user, campana_id=payload.campana_id)
    try:
        return asistente_plantillas.generar_plantilla(
            descripcion_usuario=payload.descripcion_usuario,
            contexto=payload.contexto,
            user_id=current_user.usuario,
            campos_contexto=call_details.campos_de_campana(engine, payload.campana_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Error inesperado al generar la plantilla: {e}")

#* Revisión integral de la plantilla (mejora masiva)
# `mejorar-prompt` solo reescribe UN texto. Esto mira la plantilla ENTERA y puede
# proponer cambios de ESTRUCTURA: tipo de dato de un atributo, opciones de sus listas,
# marca de opcional, ponderación, atributos que faltan y (si se habilita) los que
# sobran. Sigue el mismo contrato de dos pasos que el resto del asistente: revisar no
# escribe nada, y aplicar recibe SOLO lo que el usuario dejó tildado en pantalla.
def _plantilla_para_revision(plantilla_id: int, provista: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Estado de la plantilla que se le manda a la IA.

    Los ATRIBUTOS salen siempre de la BD: son la única fuente donde sus IDs son reales
    (el editor los guarda de a uno, así que nunca hay atributos sin guardar) y así la
    propuesta no puede terminar apuntando a un atributo de otra plantilla. De lo que
    manda el editor se toma solo la CABECERA, que sí puede tener cambios todavía sin
    guardar y es lo que el usuario está viendo."""
    guardada = plantillas_manager_instance.obtener_plantilla(plantilla_id)
    if guardada is None:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    provista = provista or {}
    def _texto(campo: str, alterno: Optional[str] = None) -> str:
        propuesto = provista.get(campo)
        if isinstance(propuesto, str) and propuesto.strip():
            return propuesto
        return guardada.get(campo) or guardada.get(alterno or campo) or ""
    return {
        "nombre": _texto("nombre"),
        "descripcion": _texto("descripcion"),
        # sp_ObtenerPlantillaCompleta devuelve el system prompt como 'system'.
        "system_prompt": _texto("system_prompt", "system"),
        "recordatorio": _texto("recordatorio"),
        "empresa": provista.get("empresa"),
        "campana": provista.get("campana"),
        "atributos": guardada.get("atributos") or [],
    }


# La revisión NO se resuelve dentro de la request: se lanza y se consulta por polling.
# Encadena las consultas de evidencia y un llamado a Gemini con razonamiento HIGH sobre la
# plantilla entera —decenas de segundos, más de un minuto en una plantilla grande— y el
# frontend corre bajo gunicorn, que mata al worker a los 30s y devuelve SU página de error
# (un "Internal Server Error" en HTML que el JS ni siquiera puede parsear). Mismo problema
# y misma solución que la cola del asistente de documentación (app/doc_jobs.py).
@router.post("/plantillas/{plantilla_id}/ia/revisar", tags=["plantillas"], status_code=202,
             dependencies=[Depends(require_template_create)])
def ia_revisar_plantilla(plantilla_id: int, payload: RevisarPlantillaRequest,
                         current_user: User = Depends(get_current_active_user)):
    """Lanza la revisión de la plantilla completa y devuelve el id para seguirla.

    No modifica nada: el resultado es un plan de cambios que el usuario acepta o descarta.

    Antes de preguntarle a la IA se junta la EVIDENCIA: qué respondió esta plantilla
    auditando en los últimos meses y en qué la corrigieron los auditores. Sin eso la
    revisión opina sobre el texto; con eso puede decir "el 41% de las respuestas cae en
    Otros" o "los auditores te corrigen este criterio 1 de cada 2 veces".
    """
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    usuario_id = current_user.usuario

    def _trabajo():
        evidencia = None
        if payload.usar_evidencia:
            # Best-effort por dentro: si la consulta falla, devuelve vacío y la revisión
            # sigue siendo la de antes (sobre el texto).
            evidencia = evidencia_plantilla.evidencia_de_plantilla(engine, plantilla_id)
        return asistente_plantillas.revisar_plantilla(
            plantilla=_plantilla_para_revision(plantilla_id, payload.plantilla),
            instruccion_usuario=payload.instruccion_usuario,
            foco=payload.foco,
            permitir_eliminar=payload.permitir_eliminar,
            evidencia=evidencia,
            user_id=usuario_id,
            campos_contexto=call_details.campos_de_plantilla(engine, plantilla_id),
        )

    try:
        job_id = revision_jobs.lanzar("revisar_plantilla", usuario_id, plantilla_id, _trabajo)
    except RuntimeError as e:
        # No se pudo registrar el trabajo (permisos/disco). Mejor fallar acá con el motivo
        # que devolver un id que el polling no va a encontrar nunca.
        raise HTTPException(status_code=500, detail=str(e))
    return {"job_id": job_id, "estado": revision_jobs.ESTADO_EN_CURSO}


@router.get("/plantillas/ia/revision/{job_id}", tags=["plantillas"],
            dependencies=[Depends(require_template_create)])
def ia_estado_revision(job_id: str, current_user: User = Depends(get_current_active_user)):
    """Estado de una revisión lanzada. Es lo que consulta el navegador mientras espera.

    `desconocido` no es un error del servidor: el trabajo venció o su registro se perdió.
    Ya NO pasa por caer en otro worker —el estado vive en disco, compartido por todos (ver
    app/revision_jobs.py)—, que fue el bug de la primera versión. El editor lo traduce a
    "volvé a pedirlo" en vez de dejar el spinner girando para siempre.

    Ruta de 4 segmentos: no colisiona con "/plantillas/ia/{plantilla_id}".
    """
    job = revision_jobs.obtener(job_id)
    if job is None:
        return {"job_id": job_id, "estado": "desconocido"}
    # Un id es imposible de adivinar, pero el resultado puede traer los prompts completos
    # de una plantilla: solo lo ve quien la pidió.
    if job.get("usuario_id") is not None and job["usuario_id"] != current_user.usuario:
        raise HTTPException(status_code=403, detail="Esa revisión no es tuya.")
    if job.get("plantilla_id"):
        _exigir_scope(current_user, plantilla_id=job["plantilla_id"])
    return revision_jobs.para_respuesta(job)


@router.post("/plantillas/{plantilla_id}/ia/revisar-atributo", tags=["plantillas"], status_code=202,
             dependencies=[Depends(require_template_create)])
def ia_revisar_atributo(plantilla_id: int, payload: RevisarAtributoRequest,
                        current_user: User = Depends(get_current_active_user)):
    """Lanza otra versión de UN cambio propuesto. Se consulta con el mismo polling.

    Es más corto que la revisión completa (un solo atributo), pero también llama a Gemini
    con razonamiento HIGH: pasa de largo los 30 segundos del worker igual de fácil.

    Sin esto, una tarjeta que no convence solo se podía descartar y volver a revisar la
    plantilla entera —otro llamado caro, y encima se perdía el resto de la propuesta."""
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    if not (payload.instruccion_usuario or "").strip():
        # Se valida acá y no adentro del hilo: es un error del pedido, tiene que volver
        # como 400 en el acto y no como un trabajo que arranca para fallar.
        raise HTTPException(status_code=400, detail="Decile qué querés distinto para que pueda proponer otra versión.")
    usuario_id = current_user.usuario

    def _trabajo():
        evidencia = None
        if payload.usar_evidencia:
            evidencia = evidencia_plantilla.evidencia_de_plantilla(engine, plantilla_id)
        return asistente_plantillas.revisar_atributo(
            plantilla=_plantilla_para_revision(plantilla_id, None),
            atributo_id=payload.atributo_id,
            instruccion_usuario=payload.instruccion_usuario,
            propuesta_previa=payload.propuesta_previa,
            accion=payload.accion,
            evidencia=evidencia,
            user_id=usuario_id,
            campos_contexto=call_details.campos_de_plantilla(engine, plantilla_id),
        )

    try:
        job_id = revision_jobs.lanzar("revisar_atributo", usuario_id, plantilla_id, _trabajo)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"job_id": job_id, "estado": revision_jobs.ESTADO_EN_CURSO}


def _atributo_a_dict(attr: AtributoRevisado, orden: int) -> Dict[str, Any]:
    return {
        "nombre": attr.nombre,
        "prompt": attr.prompt,
        "tipo": attr.tipo,
        "restricciones": attr.restricciones,
        "orden": attr.orden if attr.orden is not None else orden,
        "DarAviso": bool(attr.DarAviso),
        "FrasesAviso": attr.FrasesAviso,
        "ponderacion": attr.ponderacion if attr.ponderacion is not None else 0,
        "es_opcional": bool(attr.es_opcional),
    }


@router.post("/plantillas/{plantilla_id}/ia/aplicar-revision", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def ia_aplicar_revision(plantilla_id: int, payload: AplicarRevisionRequest,
                        current_user: User = Depends(get_current_active_user)):
    """Aplica los cambios de la revisión que el usuario aceptó.

    Cada cambio se guarda por separado y se informa qué se aplicó y qué falló: son N
    operaciones sobre N atributos y una que rebota (por ejemplo, un atributo de texto
    que el freno de transcripción rechaza) no tiene por qué tirar abajo el resto. El
    orden importa: primero la cabecera, después las ediciones, después las bajas y al
    final las altas, para que los atributos nuevos queden al fondo de la lista."""
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    existentes = plantillas_manager_instance.atributos_activos(plantilla_id)
    errores: List[Dict[str, str]] = []
    aplicados = 0

    # Foto del estado PREVIO antes de tocar nada: una revisión toca varios atributos de
    # una sola vez y hasta acá no había a dónde volver (calidad.PlantillaVersiones solo
    # se escribía cuando la plantilla AUDITA, no al guardar en el editor). Con esto el
    # editor puede ofrecer "Deshacer esta revisión" apuntando a esta versión.
    version_previa = versionado.obtener_o_crear_version(
        engine, plantilla_id, user_id=current_user.usuario,
        motivo="Estado previo a una revisión con IA",
    )

    if payload.cabecera is not None:
        try:
            plantillas_manager_instance.modificar_plantilla(
                plantilla_id=plantilla_id,
                nombre=payload.cabecera.nombre,
                descripcion=payload.cabecera.descripcion,
                system_prompt=payload.cabecera.system_prompt,
                recordatorio=payload.cabecera.recordatorio,
            )
            aplicados += 1
        except Exception as e:  # noqa: BLE001
            errores.append({"ref": "Cabecera de la plantilla", "detalle": str(e)})

    for attr in payload.modificar:
        # Un AtributoID que no es de ESTA plantilla no se toca: el alcance por empresa se
        # validó sobre la plantilla del path, no sobre los ids sueltos del body.
        if attr.id is None or attr.id not in existentes:
            errores.append({"ref": attr.nombre or f"Atributo {attr.id}",
                            "detalle": "El atributo no pertenece a esta plantilla (o ya no está activo)."})
            continue
        try:
            plantillas_manager_instance.modificar_atributo(
                atributo_id=attr.id,
                nombre=attr.nombre,
                prompt=attr.prompt,
                tipo=attr.tipo,
                restricciones=attr.restricciones,
                orden=attr.orden,
                DarAviso=attr.DarAviso,
                FrasesAviso=attr.FrasesAviso,
                ponderacion=attr.ponderacion,
                es_opcional=attr.es_opcional,
            )
            aplicados += 1
        except Exception as e:  # noqa: BLE001
            errores.append({"ref": attr.nombre or f"Atributo {attr.id}", "detalle": str(e)})

    for atributo_id in payload.eliminar:
        if atributo_id not in existentes:
            errores.append({"ref": f"Atributo {atributo_id}",
                            "detalle": "El atributo no pertenece a esta plantilla (o ya no está activo)."})
            continue
        try:
            plantillas_manager_instance.desactivar_atributo(atributo_id)
            aplicados += 1
        except Exception as e:  # noqa: BLE001
            errores.append({"ref": existentes[atributo_id]["nombre"], "detalle": str(e)})

    ordenes = [o["orden"] for o in existentes.values() if isinstance(o.get("orden"), int)]
    siguiente_orden = (max(ordenes) + 1) if ordenes else 0
    for i, attr in enumerate(payload.agregar):
        # De a uno y no en lote: así un atributo rechazado (por ejemplo, texto libre que
        # pide la transcripción) no se lleva puestas también las altas que sí son válidas.
        try:
            plantillas_manager_instance.crear_atributos_plantilla(
                plantilla_id, [_atributo_a_dict(attr, siguiente_orden + i)]
            )
            aplicados += 1
        except Exception as e:  # noqa: BLE001
            errores.append({"ref": attr.nombre or "Atributo nuevo", "detalle": str(e)})

    total = (1 if payload.cabecera is not None else 0) + len(payload.modificar) + \
        len(payload.eliminar) + len(payload.agregar)
    # `version_previa` es None si la migración de versionado no está aplicada: en ese
    # caso el editor simplemente no ofrece deshacer.
    return {"aplicados": aplicados, "total": total, "errores": errores,
            "version_previa": version_previa}


@router.post("/skills/asignar", tags=["plantillas"], dependencies=[Depends(require_template_create)])
def asignar_skill(campana_id: int, skills: List[str], current_user: User = Depends(get_current_active_user)):
    _exigir_scope(current_user, campana_id=campana_id)
    try:
        plantillas_manager_instance.asignar_skill(campana_id, skills)
        return {"status": "success"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------- #
# Historial de versiones de la plantilla (Golden Set — Fase 2)                  #
# ---------------------------------------------------------------------------- #
# `sp_ModificarPlantilla` pisa el texto del prompt y no deja rastro. Sin historial
# no se puede atribuir una mejora ("la v3 subió el kappa de 0.51 a 0.68"), no hay
# a dónde volver cuando un cambio empeora las auditorías, y la verdad humana del
# Golden Set envejece en silencio cuando la campaña cambia un criterio.
#
# OJO: una versión se crea SOLA cuando la plantilla AUDITA (o cuando alguien revisa
# una auditoría suya), no al guardar en el editor. Ver AuditorIA/versionado.py. Por eso
# el endpoint devuelve `estado_actual`: es lo que le permite a la pantalla decir
# "esto que estás viendo todavía no auditó nada" en vez de mostrar un historial
# desactualizado sin explicación. El POST de acá abajo es la contracara: guardar ese
# estado a mano, para tener a dónde volver sin pagar una corrida.
#
# Viven en este router y no en el de auditorías porque el público es el del editor
# de plantillas: el router de auditorías exige audit:execute, que quien solo edita
# plantillas (template:read/template:create) no tiene por qué tener.
@router.get("/plantillas/{plantilla_id}/versiones", tags=["plantillas"])
def listar_versiones_plantilla(plantilla_id: int, current_user: User = Depends(get_current_active_user)):
    """Historial de versiones, con cuántas auditorías produjo cada una.

    El conteo es lo que vuelve útil el historial: una versión con 3 auditorías no
    se puede comparar contra una con 800.
    """
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    actual = versionado.version_actual(engine, plantilla_id)
    return {
        "versiones": versionado.listar_versiones(engine, plantilla_id),
        "actual": actual,
        # None = el estado guardado en el editor todavía no produjo ninguna
        # auditoría, así que no está versionado. No es un error: es el diseño.
        "estado_actual_versionado": actual is not None,
    }


# calidad.PlantillaVersiones.Motivo es NVARCHAR(1000): se recorta acá para que un
# texto largo no reviente el INSERT con un error de base.
MOTIVO_VERSION_MAX = 1000
MOTIVO_VERSION_DEFAULT = "Guardada a mano desde el editor"


@router.post("/plantillas/{plantilla_id}/versiones", tags=["plantillas"],
             dependencies=[Depends(require_template_create)])
def guardar_version_plantilla(plantilla_id: int,
                              payload: Optional[GuardarVersionRequest] = None,
                              current_user: User = Depends(get_current_active_user)):
    """Guarda el estado actual de la plantilla en el historial, sin auditar.

    El alta automática (al auditar) deja un hueco: entre que se edita una plantilla y
    que corre la próxima auditoría no hay punto de retorno, y forzar una corrida solo
    para tener uno cuesta plata. Este endpoint es ese punto de retorno a pedido.

    Sigue siendo la misma alta por hash: guardar dos veces sin tocar nada NO crea dos
    versiones, devuelve la que ya estaba con `creada` en False. Por eso la respuesta
    trae `creada` — es lo que le permite a la pantalla distinguir "guardé la v5" de
    "esto ya era la v4" en vez de mentir un alta.

    Escribe en el historial de la plantilla: exige template:create como el resto de las
    escrituras del editor.
    """
    _exigir_scope(current_user, plantilla_id=plantilla_id)

    motivo = ((payload.motivo if payload else None) or "").strip()[:MOTIVO_VERSION_MAX]
    try:
        datos = versionado.guardar_version_actual(
            engine, plantilla_id,
            user_id=current_user.usuario,
            motivo=motivo or MOTIVO_VERSION_DEFAULT,
        )
    except versionado.VersionadoNoDisponible as e:
        # 409 y no 500: la base está sana, lo que falta es la migración. El texto va
        # tal cual a la pantalla para que quien lo vea sepa qué pedir.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("No se pudo guardar la versión de la plantilla %s", plantilla_id)
        raise HTTPException(status_code=500, detail=f"No se pudo guardar la versión: {e}")

    if datos is None:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return datos


def _restricciones_de_snapshot(valor: Any) -> Optional[Dict[str, Any]]:
    """Las restricciones se guardan en el snapshot tal como están en la columna (texto
    JSON); el manager espera un dict."""
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            parseado = json.loads(valor)
            return parseado if isinstance(parseado, dict) else None
        except ValueError:
            return None
    return None


@router.post("/plantillas/{plantilla_id}/versiones/{version_id}/restaurar", tags=["plantillas"],
             dependencies=[Depends(require_template_create)])
def restaurar_version_plantilla(plantilla_id: int, version_id: int,
                                current_user: User = Depends(get_current_active_user)):
    """Devuelve la plantilla al estado de una versión. Es el "deshacer" del editor.

    Restaurar significa dejar la plantilla EXACTAMENTE como estaba: los atributos que se
    editaron vuelven a sus valores, los que se dieron de baja **reviven en su misma fila**
    (no se recrean: así no se pierde el enlace con las auditorías que ya los
    respondieron) y los que se agregaron después se dan de baja.

    Lo que el snapshot NO guarda —el orden, las alertas por mail y la descripción, que
    no cambian lo que la IA responde (ver AuditorIA/versionado.py::CAMPOS_ATRIBUTO)— se
    reenvía tal como está hoy, para no moverlo de rebote.

    Antes de restaurar se versiona el estado actual, así que restaurar también se puede
    deshacer.
    """
    _exigir_scope(current_user, plantilla_id=plantilla_id)
    version = versionado.obtener_version(engine, version_id)
    if version is None or not version.get("snapshot"):
        raise HTTPException(status_code=404, detail="Versión no encontrada.")
    if int(version["PlantillaID"]) != plantilla_id:
        raise HTTPException(status_code=400, detail="Esa versión no es de esta plantilla.")

    snapshot = version["snapshot"]
    actual = plantillas_manager_instance.obtener_plantilla(plantilla_id)
    if actual is None:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    version_previa = versionado.obtener_o_crear_version(
        engine, plantilla_id, user_id=current_user.usuario,
        motivo=f"Estado previo a restaurar la v{version.get('Numero')}",
    )

    errores: List[Dict[str, str]] = []
    aplicados = 0
    revividos = 0
    dados_de_baja = 0

    try:
        plantillas_manager_instance.modificar_plantilla(
            plantilla_id=plantilla_id,
            nombre=snapshot.get("nombre"),
            # No está en el snapshot: se reenvía la de hoy para no vaciarla.
            descripcion=actual.get("descripcion"),
            system_prompt=snapshot.get("system_prompt"),
            recordatorio=snapshot.get("recordatorio"),
        )
        aplicados += 1
    except Exception as e:  # noqa: BLE001
        errores.append({"ref": "Cabecera de la plantilla", "detalle": str(e)})

    en_la_tabla = plantillas_manager_instance.atributos_de_plantilla(plantilla_id, incluir_inactivos=True)
    del_snapshot = {int(a["atributo_id"]): a for a in (snapshot.get("atributos") or [])}

    for atributo_id, attr in del_snapshot.items():
        meta = en_la_tabla.get(atributo_id)
        try:
            if meta is None:
                # La fila ya no existe (no debería pasar: las bajas son lógicas). Se
                # recrea como atributo nuevo para no perder el criterio.
                plantillas_manager_instance.crear_atributos_plantilla(plantilla_id, [{
                    "nombre": attr.get("nombre"), "prompt": attr.get("prompt"),
                    "tipo": attr.get("tipo"),
                    "restricciones": _restricciones_de_snapshot(attr.get("restricciones")),
                    "orden": len(en_la_tabla), "DarAviso": False, "FrasesAviso": None,
                    "ponderacion": attr.get("ponderacion") or 0,
                    "es_opcional": bool(attr.get("es_opcional")),
                }])
                aplicados += 1
                continue
            if not meta["activo"]:
                plantillas_manager_instance.reactivar_atributo(atributo_id, plantilla_id)
                revividos += 1
            plantillas_manager_instance.modificar_atributo(
                atributo_id=atributo_id,
                nombre=attr.get("nombre"),
                prompt=attr.get("prompt"),
                tipo=attr.get("tipo"),
                restricciones=_restricciones_de_snapshot(attr.get("restricciones")),
                orden=meta["orden"],
                DarAviso=meta["DarAviso"],
                FrasesAviso=meta["FrasesAviso"],
                ponderacion=attr.get("ponderacion"),
                es_opcional=bool(attr.get("es_opcional")),
            )
            aplicados += 1
        except Exception as e:  # noqa: BLE001
            errores.append({"ref": attr.get("nombre") or f"Atributo {atributo_id}", "detalle": str(e)})

    for atributo_id, meta in en_la_tabla.items():
        if meta["activo"] and atributo_id not in del_snapshot:
            try:
                plantillas_manager_instance.desactivar_atributo(atributo_id)
                dados_de_baja += 1
                aplicados += 1
            except Exception as e:  # noqa: BLE001
                errores.append({"ref": meta["nombre"], "detalle": str(e)})

    # Conocimiento de referencia: la versión dice qué documentos leía la IA, y un
    # snapshot sin la clave es una versión sin conocimiento. Se restaura la ELECCIÓN:
    # el contenido de cada documento vive en su chatbot y no vuelve atrás desde acá.
    try:
        conocimiento_plantilla.guardar_seleccion(
            engine, plantilla_id,
            [d.get("doc_id") for d in (snapshot.get("conocimiento") or [])],
            user_id=current_user.usuario, solo_activos=False,
        )
    except conocimiento_plantilla.ConocimientoNoDisponible:
        pass  # sin la migración no hay selección que restaurar
    except Exception as e:  # noqa: BLE001
        errores.append({"ref": "Conocimiento de referencia", "detalle": str(e)})

    return {
        "aplicados": aplicados,
        "revividos": revividos,
        "dados_de_baja": dados_de_baja,
        "errores": errores,
        "numero": version.get("Numero"),
        "version_previa": version_previa,
    }


@router.get("/plantillas/versiones/{version_id}", tags=["plantillas"])
def obtener_version_plantilla(version_id: int, current_user: User = Depends(get_current_active_user)):
    """Snapshot completo de una versión (system prompt, recordatorio, atributos)."""
    version = versionado.obtener_version(engine, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Versión no encontrada.")
    _exigir_scope(current_user, plantilla_id=version["PlantillaID"])
    return version


@router.get("/plantillas/versiones/{version_a}/diff/{version_b}", tags=["plantillas"])
def diff_versiones_plantilla(version_a: int, version_b: int,
                             current_user: User = Depends(get_current_active_user)):
    """Qué cambió entre dos versiones: cabecera y atributos agregados/quitados/modificados."""
    version = versionado.obtener_version(engine, version_a)
    if version is None:
        raise HTTPException(status_code=404, detail="Versión no encontrada.")
    _exigir_scope(current_user, plantilla_id=version["PlantillaID"])
    return versionado.diff_versiones(engine, version_a, version_b)
