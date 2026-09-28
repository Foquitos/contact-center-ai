import logging
import re
from typing import List, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _obtener_plataforma(engine: Engine, campana_id: int) -> Optional[str]:
    with engine.connect() as conn:
        res = conn.execute(
            text(
                "SELECT p.Nombre FROM calidad.Campanas c "
                "JOIN calidad.Plataformas p ON c.PlataformaID = p.PlataformaID "
                "WHERE c.CampanaID = :cid"
            ),
            {"cid": campana_id},
        ).fetchone()
    return res[0] if res else None


def _normalizar_separadores(texto: str) -> str:
    """Unifica los separadores de la ruta (' -> ', ' - ' y '- ') en uno solo.

    El árbol de tipificaciones parte el string de la DB por ambos separadores, pero
    arma el `value` de los nodos intermedios con ' - '. Comparar sin normalizar deja
    afuera las ramas de las campañas que usan ' -> ' en la DB (caso Odonto Plus).
    Benefix tiene wrap-ups sin espacio antes del guion ("MDF- Solicitud de Evento")
    que el árbol cuelga del grupo "MDF": sin normalizar, tildar "MDF" no los traía.
    Un guion pegado a los dos lados ("completa-Clinicas") no es separador y no se toca.
    """
    return re.sub(r'(?<=\S)- ', ' - ', texto.replace(' -> ', ' - '))


def _coincide_rama(tipificacion: str, seleccion: str) -> bool:
    """True si `tipificacion` es la rama seleccionada, cuelga de ella o contiene la sub-rama/hoja.

    Soporta:
      - Coincidencia exacta (`t == s`)
      - Rama desde la raíz (`t.startswith(s + ' - ')`)
      - Hoja o sub-rama sin prefijo de campaña (`t.endswith(' - ' + s)`)
      - Rama intermedia sin prefijo de campaña (`f' - {s} - ' in t`)

    Se exige que los límites coincidan con separadores para no arrastrar
    hermanos con nombres parecidos ('Venta' no debe traer 'Ventas -> ...').
    """
    t = _normalizar_separadores(tipificacion)
    s = _normalizar_separadores(seleccion)
    return (
        t == s
        or t.startswith(s + ' - ')
        or t.endswith(' - ' + s)
        or (f' - {s} - ' in t)
    )


def expandir_seleccion_mitrol(engine: Engine, campana_id_str: str, seleccion: List[str]) -> List[str]:
    """Expande nodos padre/intermedios a todas las tipificaciones de la DB que comiencen con ellos.

    Replica la lógica usada en el endpoint de auditoría manual para que el scheduler
    encuentre tipificaciones cuando el usuario selecciona ramas del árbol y no hojas exactas.

    Reporte de Magali (2026-08-20, Odonto Plus): tildar la carpeta intermedia
    "Agenda completa-Clinicas Dental" traía 0 llamados aunque había 4 entre el 14 y el
    18/08. El nodo intermedio viaja como "No obtuvo turno - Agenda completa-Clinicas
    Dental" (el árbol junta la ruta con ' - ') y en la DB la tipificación usa ' -> ',
    así que el `startswith` crudo nunca pegaba: la selección caía al fallback literal y
    el IN de SQL no encontraba nada. Las hojas y las carpetas de nivel 1 funcionaban
    (no tienen separador en el value), por eso el bug era invisible salvo en las ramas
    intermedias.
    """
    try:
        campana_id = int(campana_id_str)

        plataforma_nombre = _obtener_plataforma(engine, campana_id)
        if plataforma_nombre is None:
            raise ValueError(f"No se encontró la campaña con id {campana_id}")

        todas_las_tipificaciones: List[str] = []
        if plataforma_nombre == 'Mitrol':
            query_all = text(
                """
                SELECT DISTINCT d.Tipificación
                FROM detalle_de_interacciones_por_campana_lote d
                JOIN calidad.Skills s ON s.Nombre = d.Campaña AND s.IsActive = 1
                WHERE fecha_inicio > DATEADD(Day, -10, GETDATE())
                AND s.CampanaID = :campana_id
                """
            )
            with engine.connect() as conn:
                result = conn.execute(query_all, {"campana_id": campana_id}).fetchall()
                todas_las_tipificaciones = [row[0] for row in result]
        elif plataforma_nombre == 'Wize':
            query_all = text(
                """
                SELECT DISTINCT l.subtipo_interaccion
                FROM [Vitalis_Llamadas] l
                WHERE fecha_inicio > DATEADD(Day, -10, GETDATE())
                """
            )
            with engine.connect() as conn:
                result = conn.execute(query_all).fetchall()
                todas_las_tipificaciones = [row[0] for row in result]
        elif plataforma_nombre == 'Genesys':
            query_all = text(
                """
                SELECT DISTINCT i.Tipificacion
                FROM [Acme].[Benefix].[Interacciones] i
                JOIN calidad.Skills s ON s.Nombre = i.Cola AND s.IsActive = 1
                WHERE i.Fecha > DATEADD(Day, -10, GETDATE())
                AND i.Tipificacion IS NOT NULL
                AND s.CampanaID = :campana_id
                """
            )
            with engine.connect() as conn:
                result = conn.execute(query_all, {"campana_id": campana_id}).fetchall()
                todas_las_tipificaciones = [row[0] for row in result]
        else:
            return list(seleccion)

        seleccion_expandida = set()
        for item_seleccionado in seleccion:
            matches = [t for t in todas_las_tipificaciones if _coincide_rama(t, item_seleccionado)]
            if matches:
                seleccion_expandida.update(matches)
            else:
                seleccion_expandida.add(item_seleccionado)
        return list(seleccion_expandida)
    except Exception as e:
        logger.error(f"Error expandiendo tipificaciones Mitrol/Wize: {e}")
        return list(seleccion)


def aplicar_variantes_separador(tipificacion: List[str]) -> List[str]:
    """Genera variantes con separador ' - ' y ' -> ' para tolerar ambos formatos de la DB."""
    variantes = set()
    for t in tipificacion:
        variantes.add(t)
        t_guiones = t.replace(' -> ', ' - ').replace('->', '-')
        t_flechas = t_guiones.replace(' - ', ' -> ')
        variantes.add(t_guiones)
        variantes.add(t_flechas)
    return list(variantes)


def preparar_tipificacion_para_consulta(
    engine: Engine,
    tipificacion: Optional[List[str]],
    campana: Optional[str],
) -> Optional[List[str]]:
    """Pipeline completo: expansión por prefijo + variantes de separador.

    Devuelve la lista lista para usar en el filtro SQL. Si no hay tipificación
    o no hay campaña, no hace nada.
    """
    if not tipificacion:
        return tipificacion

    if campana:
        try:
            plataforma = _obtener_plataforma(engine, int(campana))
            if plataforma in ('Mitrol', 'Wize', 'Genesys'):
                logger.info(f"Campaña {plataforma} detectada. Expandiendo tipificaciones: {tipificacion}")
                tipificacion = expandir_seleccion_mitrol(engine, campana, tipificacion)
                logger.info(f"Selección expandida ({len(tipificacion)} items).")
        except Exception as e:
            logger.error(f"Error en bloque de expansión de tipificaciones: {e}")

    tipificacion = aplicar_variantes_separador(tipificacion)
    logger.info(f"Tipificaciones con variantes para la BD: {len(tipificacion)} items")
    return tipificacion
