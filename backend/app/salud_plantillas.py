"""Reporte semanal de salud de las plantillas de auditoría: qué está mal y quién lo usa.

POR QUÉ EXISTE
--------------
El chequeo de salud ya existía como botón (GET /plantillas/salud/{campana_id}): corre
las mismas señales que la revisión con IA, sobre la campaña entera, sin gastar un token.
El problema es que hay que acordarse de apretarlo. La medición de adopción de agosto de
2026 fue clara: lo que depende de que alguien lo abra, no se abre — el asistente de
prompts tuvo 24 usos reales en 7 semanas entre 39 personas habilitadas.

Así que el mismo chequeo sale solo, una vez por semana, hacia las jefaturas de Calidad:
la lista de plantillas con problemas, ordenada por gravedad y por uso real, con el
detalle de qué arreglar en cada una.

Y ADEMÁS ES EL INSTRUMENTO
--------------------------
El reporte contesta una pregunta que hasta ahora no tenía respuesta: si el pedido "no
baja" o si baja y no se aplica. Si semana tras semana aparecen las MISMAS plantillas en
rojo, ya no es un problema de comunicación. Ese contraste es el único dato con el que se
puede reclamar sin discutir impresiones de reunión.

COSTO
-----
Cero tokens: las señales son estructura y texto (ver AuditorIA/senales_prompt.py y
asistente_plantillas.senales_de_atributo). Lo único que toca la base es una consulta
agregada por plantilla + la lectura de los atributos de cada una, una vez por semana.

Best-effort, como el resto de los avisos automáticos: si algo falla se loguea y el
scheduler sigue.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.managers import plantillas_manager_instance
from AuditorIA import asistente_plantillas
from AuditorIA import call_details

logger = logging.getLogger(__name__)

# Cuántos TIPOS de problema se detallan por plantilla, y cuántos atributos se nombran en
# cada uno. El mail tiene que caber en una pantalla: si una plantilla tiene 12 problemas
# distintos, el mensaje ya es "abrila y miralas todas", no la lista completa.
MAX_PROBLEMAS_POR_PLANTILLA = 4
MAX_ATRIBUTOS_POR_PROBLEMA = 6

# Ventana para decidir si una plantilla "se está usando". Una rota que corre todos los
# días es urgente; una rota que no audita hace meses es limpieza.
DIAS_USO = 30


def _destinatarios() -> List[str]:
    """Jefaturas de Calidad + ADMIN_EMAIL. Sin destinatarios no se manda nada.

    Se configura por `PLANTILLAS_SALUD_DESTINATARIOS` (separados por ';'), igual que las
    alertas de presupuesto. Va en config y no en una tabla porque es una lista corta y
    estable, y porque tenerla versionada deja asentado a quién se le está avisando.
    """
    crudo = getattr(settings, "PLANTILLAS_SALUD_DESTINATARIOS", "") or ""
    destinatarios = [d.strip() for d in crudo.split(";") if d.strip()]
    if settings.ADMIN_EMAIL and settings.ADMIN_EMAIL not in destinatarios:
        destinatarios.append(settings.ADMIN_EMAIL)
    return destinatarios


def _plantillas_activas(conn) -> List[Dict[str, Any]]:
    """Todas las plantillas activas con su campaña, su empresa y su uso reciente.

    El uso va en la misma consulta (OUTER APPLY) y no en una segunda pasada: es lo que
    ordena el reporte, y sin él la lista es un inventario de problemas sin prioridad.
    """
    filas = conn.execute(text(f"""
        SELECT p.PlantillaID, p.Nombre AS Plantilla,
               c.CampanaID, c.Nombre AS Campana,
               e.EmpresaID, e.Nombre AS Empresa,
               u.auditorias_30d
        FROM calidad.Plantillas p
        JOIN calidad.Campanas c ON c.CampanaID = p.CampanaID
        JOIN calidad.Empresas e ON e.EmpresaID = c.EmpresaID
        OUTER APPLY (
            SELECT COUNT(*) AS auditorias_30d
            FROM calidad.Auditorias a
            WHERE a.PlantillaID = p.PlantillaID
              AND a.IsActive = 1
              AND a.FechaAuditoria >= DATEADD(day, -{DIAS_USO}, GETDATE())
        ) u
        WHERE p.IsActive = 1 AND c.IsActive = 1 AND e.IsActive = 1
    """)).mappings().all()
    return [dict(f) for f in filas]


def relevar() -> Dict[str, Any]:
    """Corre el chequeo sobre TODAS las plantillas activas del sistema.

    Devuelve la lista ordenada de peor a mejor, y dentro de la misma gravedad, de más
    usada a menos: lo que hay que arreglar primero es lo que está roto y encima audita
    todos los días.
    """
    with engine.connect() as conn:
        plantillas = _plantillas_activas(conn)

    relevadas: List[Dict[str, Any]] = []
    for fila in plantillas:
        plantilla_id = int(fila["PlantillaID"])
        try:
            datos = plantillas_manager_instance.obtener_plantilla(plantilla_id)
        except RuntimeError as e:
            logger.warning("No se pudo leer la plantilla %s para el reporte de salud: %s", plantilla_id, e)
            continue
        if not datos:
            continue

        atributos = asistente_plantillas.normalizar_atributos(datos.get("atributos"))
        # Qué datos del llamado recibe la IA además del audio en esta campaña: sin esto la
        # señal `pide_dato_externo` reporta como problema un prompt que se apoya en un
        # campo que SÍ viaja con el audio (ver AuditorIA/call_details.py). El nombre de la
        # empresa ya vino en la consulta de arriba, así que no cuesta una query más.
        campos = call_details.campos_de_empresa(fila["Empresa"])
        senales = asistente_plantillas.senales_de_plantilla(atributos, campos_contexto=campos)
        altas = [s for s in senales if s["severidad"] == asistente_plantillas.SEVERIDAD_ALTA]
        relevadas.append({
            "plantilla_id": plantilla_id,
            "plantilla": fila["Plantilla"],
            "campana": fila["Campana"],
            "empresa": fila["Empresa"],
            "atributos": len(atributos),
            "auditorias_30d": int(fila["auditorias_30d"] or 0),
            "senales": senales,
            "altas": altas,
            "medias": [s for s in senales if s["severidad"] != asistente_plantillas.SEVERIDAD_ALTA],
            "estado": "alta" if altas else ("media" if senales else "ok"),
        })

    # Dentro de la misma gravedad manda el USO, no la cantidad de problemas: una plantilla
    # con un solo problema que audita 3.000 llamados por mes está midiendo mal 3.000
    # llamados, y una con siete problemas que no audita hace meses no está midiendo nada.
    orden = {"alta": 0, "media": 1, "ok": 2}
    relevadas.sort(key=lambda p: (orden[p["estado"]], -p["auditorias_30d"],
                                  -len(p["altas"]), -len(p["medias"])))
    con_problemas = [p for p in relevadas if p["estado"] != "ok"]
    return {
        "plantillas": relevadas,
        "con_problemas": con_problemas,
        "resumen": {
            "total": len(relevadas),
            "con_problemas": len(con_problemas),
            "criticas": sum(1 for p in relevadas if p["estado"] == "alta"),
            "criticas_en_uso": sum(1 for p in relevadas
                                   if p["estado"] == "alta" and p["auditorias_30d"] > 0),
        },
    }


def _agrupar_por_problema(senales: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Junta las señales iguales de una plantilla en una sola línea.

    Cinco listas sin salida segura son UN problema que aparece cinco veces, no cinco
    problemas: repetir la misma frase cinco veces hace el mail el doble de largo y no
    agrega nada. El texto sale de la primera señal del grupo (todas dicen lo mismo) y lo
    que varía —el nombre del atributo— se lista aparte.
    """
    grupos: Dict[str, Dict[str, Any]] = {}
    for senal in senales:
        texto = re.sub(r"^'[^']*': ", "", senal.get("mensaje", ""))
        grupo = grupos.setdefault(senal["clave"], {"texto": texto, "atributos": []})
        if senal.get("atributo"):
            grupo["atributos"].append(str(senal["atributo"]))
    return list(grupos.values())


def _linea_problema(grupo: Dict[str, Any]) -> str:
    nombres = grupo["atributos"]
    if not nombres:
        return grupo["texto"]
    if len(nombres) == 1:
        return f"<b>{nombres[0]}</b>: {grupo['texto']}"
    visibles = ", ".join(f"<b>{n}</b>" for n in nombres[:MAX_ATRIBUTOS_POR_PROBLEMA])
    resto = len(nombres) - MAX_ATRIBUTOS_POR_PROBLEMA
    sufijo = f" y {resto} más" if resto > 0 else ""
    return f"{len(nombres)} atributos ({visibles}{sufijo}): {grupo['texto']}"


def _fila_html(p: Dict[str, Any]) -> str:
    color = "#c0392b" if p["estado"] == "alta" else "#e67e22"
    etiqueta = "Crítica" if p["estado"] == "alta" else "Revisar"
    uso = (f"{p['auditorias_30d']:,} auditorías".replace(",", ".")
           if p["auditorias_30d"] else "sin uso en 30 días")

    grupos = _agrupar_por_problema(p["altas"])
    detalle = [_linea_problema(g) for g in grupos[:MAX_PROBLEMAS_POR_PLANTILLA]]
    restantes = len(grupos) - len(detalle)
    if restantes > 0:
        detalle.append(f"…y {restantes} tipo(s) de problema crítico más.")
    if p["medias"]:
        detalle.append(f"<i>({len(p['medias'])} observación(es) menor(es), se ven en la plantilla)</i>")

    items = "".join(f"<li>{m}</li>" for m in detalle)
    return (
        "<tr>"
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee;vertical-align:top;">'
        f'<b style="color:{color};">{etiqueta}</b></td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee;vertical-align:top;">'
        f'<b>{p["plantilla"]}</b><br><small>{p["empresa"]} · {p["campana"]}</small></td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee;vertical-align:top;white-space:nowrap;">'
        f'{uso}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">'
        f'<ul style="margin:0;padding-left:18px;">{items}</ul></td>'
        "</tr>"
    )


def armar_html(relevamiento: Dict[str, Any]) -> str:
    """El mail lista SOLO las plantillas con problemas críticos.

    Las observaciones menores se cuentan pero no se enumeran: con 332 atributos activos
    hay siempre algo para mejorar en todas las plantillas, y un mail que las lista todas
    dice "todo está mal", que es lo mismo que no decir nada. Lo que tiene que quedar
    arriba es la lista corta de lo que hay que arreglar esta semana.
    """
    resumen = relevamiento["resumen"]
    criticas = [p for p in relevamiento["con_problemas"] if p["estado"] == "alta"]
    solo_menores = resumen["con_problemas"] - len(criticas)

    if not criticas:
        pendiente = (f" Quedan {solo_menores} con observaciones menores, que se ven "
                     "entrando a cada plantilla." if solo_menores else "")
        return (
            "<b>Salud de las plantillas de auditoría</b><br><br>"
            f"Ninguna de las {resumen['total']} plantillas activas tiene problemas críticos."
            f"{pendiente}<br><br>"
            "<small>Chequeo automático semanal. No usa IA ni consume tokens.</small>"
        )

    filas = "".join(_fila_html(p) for p in criticas)
    encabezado = (
        '<tr style="background:#f5f5f5;">'
        '<th align="left" style="padding:6px 10px;">Estado</th>'
        '<th align="left" style="padding:6px 10px;">Plantilla</th>'
        '<th align="left" style="padding:6px 10px;">Uso (30 días)</th>'
        '<th align="left" style="padding:6px 10px;">Qué revisar</th>'
        "</tr>"
    )
    en_uso = resumen["criticas_en_uso"]
    aclaracion_uso = (f", y {en_uso} de ellas están auditando hoy" if en_uso else "")
    menores = (f"<br>Otras {solo_menores} plantillas tienen observaciones menores "
               "(no listadas acá): se ven entrando a cada una." if solo_menores else "")
    return (
        "<b>Salud de las plantillas de auditoría</b><br><br>"
        f"<b>{resumen['criticas']} de {resumen['total']} plantillas activas tienen problemas "
        f"críticos</b>{aclaracion_uso}: hasta que se corrijan, esas auditorías se están "
        f"midiendo mal.{menores}<br><br>"
        f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-family:'
        f'Arial,sans-serif;font-size:13px;">{encabezado}{filas}</table><br>'
        "Se corrigen desde <b>Plantillas de auditoría</b>, entrando a la plantilla: cada "
        "atributo con problemas queda marcado, y el botón <b>Mejorar con IA</b> propone el "
        "texto corregido.<br><br>"
        "<small>Chequeo automático semanal sobre todas las plantillas activas. No usa IA "
        "ni consume tokens. «Crítica» = la IA recibe una instrucción rota (una lista sin "
        "salida segura, un criterio ponderado que no puntúa, un prompt a medio escribir).</small>"
    )


def reporte_semanal_plantillas() -> None:
    """Job semanal (run_scheduler.py). Manda el estado de todas las plantillas activas."""
    try:
        destinatarios = _destinatarios()
        if not destinatarios:
            logger.warning("Reporte de salud de plantillas: no hay destinatarios configurados.")
            return

        relevamiento = relevar()
        resumen = relevamiento["resumen"]
        if not relevamiento["con_problemas"] and not getattr(
                settings, "PLANTILLAS_SALUD_ENVIAR_SIN_HALLAZGOS", True):
            logger.info("Reporte de salud de plantillas: todo en verde, no se manda mail.")
            return

        # Import perezoso: el módulo de mail arrastra dependencias que no hacen falta
        # para relevar (mismo criterio que app/presupuesto_ia.py).
        from app.utils.mail import Envios_mail_manager

        asunto = (f"[Calidad] Plantillas para revisar: {resumen['con_problemas']} de "
                  f"{resumen['total']} ({resumen['criticas']} críticas)"
                  if relevamiento["con_problemas"]
                  else f"[Calidad] Plantillas: las {resumen['total']} activas están en verde")
        Envios_mail_manager.enviar_correo_base(
            destinatarios=destinatarios,
            asunto=asunto,
            mensaje_html=armar_html(relevamiento),
            archivos_adjuntos_memoria=[],
        )
        logger.info("Reporte de salud de plantillas enviado a %s: %s de %s con problemas (%s críticas).",
                    ", ".join(destinatarios), resumen["con_problemas"], resumen["total"],
                    resumen["criticas"])
    except Exception as e:  # noqa: BLE001
        logger.error("No se pudo enviar el reporte de salud de plantillas: %s", e, exc_info=True)
