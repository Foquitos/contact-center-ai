"""Cálculo dinámico de métricas y filtros para la pantalla de Auditar.

Permite consultar en tiempo real:
- Total de audios disponibles con los filtros activos.
- Tipificaciones disponibles con su conteo de audios (filtrando las vacías).
- Operadores activos en el período con conteo de audios y supervisor asignado.
- Conteo por sentido (Entrante, Saliente, Interno).
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Union
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.utils.tipificaciones import _coincide_rama, aplicar_variantes_separador

logger = logging.getLogger(__name__)


def _escapar_sql_in(valores: List[str]) -> str:
    """Escapa una lista de valores para una cláusula IN en T-SQL."""
    items = ["'" + str(v).replace("'", "''") + "'" for v in valores if v is not None and str(v).strip()]
    return ", ".join(items) if items else "''"


def _expandir_tipificaciones(seleccion: List[str], tipificaciones_disponibles: List[str]) -> List[str]:
    """Expande carpetas o ramas seleccionadas a todas las tipificaciones hoja disponibles en el período."""
    if not seleccion:
        return []
    expandidas = set()
    for item in seleccion:
        matches = [t for t in tipificaciones_disponibles if _coincide_rama(t, item)]
        if matches:
            expandidas.update(matches)
        else:
            expandidas.add(item)
    return aplicar_variantes_separador(list(expandidas))


def _construir_arbol_tipificaciones(tipificaciones_con_conteo: List[Dict[str, Any]], separadores: List[str]) -> List[Dict[str, Any]]:
    """Construye un árbol jerárquico a partir de tipificaciones y sus conteos.
    
    Cada nodo padre suma recursivamente la cantidad de audios de sus hijos.
    """
    arbol: List[Dict[str, Any]] = []

    def buscar_o_crear_nodo(lista_actual: List[Dict[str, Any]], nombre: str, path_completo: str) -> Dict[str, Any]:
        for nodo in lista_actual:
            if nodo['label'] == nombre:
                return nodo
        nuevo_nodo = {
            'label': nombre,
            'value': path_completo,
            'cantidad': 0,
            'children': []
        }
        lista_actual.append(nuevo_nodo)
        return nuevo_nodo

    for item in tipificaciones_con_conteo:
        tipi_texto = item['tipificacion']
        cantidad = item.get('cantidad', 0)
        if not tipi_texto:
            continue

        # Normalizar separadores a un separador común
        t_norm = tipi_texto
        for sep in separadores:
            t_norm = t_norm.replace(sep, '|||')
        
        niveles = [n.strip() for n in t_norm.split('|||') if n.strip()]
        if not niveles:
            continue

        lista_actual = arbol
        path_acumulado = []
        nodos_recorridos = []

        for i, nivel in enumerate(niveles):
            path_acumulado.append(nivel)
            path_parcial = " - ".join(path_acumulado) if i < len(niveles) - 1 else tipi_texto
            nodo = buscar_o_crear_nodo(lista_actual, nivel, path_parcial)
            nodos_recorridos.append(nodo)
            lista_actual = nodo['children']

        # Asignar la cantidad a la hoja y propagar hacia arriba
        if nodos_recorridos:
            hoja = nodos_recorridos[-1]
            hoja['cantidad'] = hoja['cantidad'] + cantidad
            for padre in nodos_recorridos[:-1]:
                padre['cantidad'] = padre['cantidad'] + cantidad

    # Ordenar alfabéticamente el árbol recursivamente
    def ordenar_arbol(nodos: List[Dict[str, Any]]):
        nodos.sort(key=lambda x: (len(x['children']) == 0, x['label'].lower()))
        for n in nodos:
            if n['children']:
                ordenar_arbol(n['children'])

    ordenar_arbol(arbol)
    return arbol


def obtener_filtros_resumen(
    conn: Connection,
    campana_id: int,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    """Calcula totales, tipificaciones con conteo y operadores para una campaña.

    Parámetros en `params`:
    - fecha_desde: str (YYYY-MM-DD) o date
    - fecha_hasta: str (YYYY-MM-DD) o date
    - duracion_min: Optional[int]
    - duracion_max: Optional[int]
    - sentido: Optional[List[str]] (Entrante, Saliente, Interno)
    - loginid: Optional[List[str]]
    - tipificacion: Optional[List[str]]
    - reauditar: Optional[bool]
    - comentario: Optional[List[str]]
    """
    # 1. Obtener plataforma y empresa de la campaña
    q_camp = text("""
        SELECT c.CampanaID, c.Nombre AS CampanaNombre, c.EmpresaID, p.Nombre AS PlataformaNombre
        FROM calidad.Campanas c
        JOIN calidad.Plataformas p ON c.PlataformaID = p.PlataformaID
        WHERE c.CampanaID = :campana_id
    """)
    camp_row = conn.execute(q_camp, {"campana_id": campana_id}).fetchone()
    if not camp_row:
        raise ValueError(f"No se encontró la campaña con ID {campana_id}")

    empresa_id = camp_row.EmpresaID
    plataforma = camp_row.PlataformaNombre

    # 2. Fechas
    fecha_desde_raw = params.get("fecha_desde")
    fecha_hasta_raw = params.get("fecha_hasta")

    hoy = date.today().strftime("%Y-%m-%d")
    fecha_hasta_str = str(fecha_hasta_raw) if fecha_hasta_raw else hoy
    fecha_desde_str = str(fecha_desde_raw) if fecha_desde_raw else fecha_hasta_str

    duracion_min = params.get("duracion_min")
    duracion_max = params.get("duracion_max")
    sentido = params.get("sentido") or []
    loginid = params.get("loginid") or []
    tipificacion = params.get("tipificacion") or []
    reauditar = bool(params.get("reauditar", False))
    comentario = params.get("comentario") or []

    # Construcción según plataforma
    if plataforma == "Mitrol":
        res = _consultar_mitrol(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar,
            comentario=comentario
        )
    elif plataforma == "CXOne":
        res = _consultar_cxone(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar
        )
    elif plataforma == "Genesys":
        res = _consultar_genesys(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar
        )
    elif plataforma == "Wize":
        res = _consultar_wize(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar
        )
    elif plataforma == "Hermes":
        res = _consultar_hermes(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar
        )
    elif plataforma == "Orion":
        res = _consultar_orion(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar
        )
    elif plataforma == "AsterVoIP":
        res = _consultar_astervoip(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar
        )
    else:
        # Fallback genérico a Mitrol
        res = _consultar_mitrol(
            conn=conn,
            campana_id=campana_id,
            empresa_id=empresa_id,
            fecha_desde=fecha_desde_str,
            fecha_hasta=fecha_hasta_str,
            duracion_min=duracion_min,
            duracion_max=duracion_max,
            sentido=sentido,
            loginid=loginid,
            tipificacion=tipificacion,
            reauditar=reauditar,
            comentario=comentario
        )

    return res


# ==============================================================================
# Implementaciones específicas por plataforma
# ==============================================================================

def _consultar_mitrol(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool,
    comentario: List[str]
) -> Dict[str, Any]:
    # 0. Pre-fetch skills activas para evitar JOIN con COLLATE en cada consulta
    q_skills = text("SELECT Nombre FROM calidad.Skills WHERE CampanaID = :campana_id AND IsActive = 1")
    skills_rows = conn.execute(q_skills, {"campana_id": campana_id}).fetchall()
    skill_nombres = [r[0] for r in skills_rows if r[0]]
    if not skill_nombres:
        return {
            "total_audios": 0,
            "tipificaciones": [],
            "operadores": [],
            "supervisores": [],
            "sentido_counts": {"Entrante": 0, "Saliente": 0, "Interno": 0},
            "es_arbol": True
        }

    where_parts = [
        f"d.Campaña IN ({_escapar_sql_in(skill_nombres)})",
        "d.fecha_inicio >= :fd_dt AND d.fecha_inicio <= :fh_dt",
        "d.Tipificación != 'Cliente No Responde'"
    ]
    params = {
        "fd_dt": f"{fecha_desde} 00:00:00",
        "fh_dt": f"{fecha_hasta} 23:59:59",
        "empresa_id": empresa_id
    }

    if not reauditar:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a "
            "WHERE a.[IdAplicativo] = g.idInteraccion + '_' + CAST(g.Segmento AS VARCHAR(2)))"
        )
    if duracion_min:
        where_parts.append(f"d.duración >= {int(duracion_min)}")
    if duracion_max:
        where_parts.append(f"d.duración <= {int(duracion_max)}")
    if sentido:
        map_sentido = {'Entrante': 'Entrante', 'Saliente': 'Saliente', 'Interno': 'Interna Saliente'}
        direcciones = [map_sentido[s] for s in sentido if s in map_sentido]
        if direcciones:
            where_parts.append(f"d.Sentido IN ({_escapar_sql_in(direcciones)})")
    if loginid:
        where_parts.append(f"d.loginId IN ({_escapar_sql_in(loginid)})")
    if comentario:
        likes = " OR ".join([f"d.CRM LIKE '%{c}%'" for c in [c.replace("'", "''") for c in comentario if c]])
        if likes:
            where_parts.append(f"({likes})")

    from_sql = """
        FROM detalle_de_interacciones_por_campana_lote d
        JOIN [Acme].[dbo].[detalle de grabaciones] g 
            ON d.idInteraccion = g.idInteraccion AND d.Segmento = g.Segmento
    """

    # 1. Conteo por tipificación (antes de filtrar por tipificación seleccionada)
    where_sql_sin_tipi = " AND ".join(where_parts)

    q_tipis = text(f"""
        SELECT d.Tipificación, COUNT(*) as cantidad
        {from_sql}
        WHERE {where_sql_sin_tipi}
          AND d.Tipificación IS NOT NULL AND d.Tipificación <> ''
        GROUP BY d.Tipificación
        ORDER BY cantidad DESC
    """)
    rows_tipis = conn.execute(q_tipis, params).fetchall()
    tipis_lista = [{"tipificacion": r[0], "cantidad": r[1]} for r in rows_tipis]
    arbol_tipis = _construir_arbol_tipificaciones(tipis_lista, [' -> ', ' - '])

    # Expansión de tipificaciones seleccionadas usando las disponibles del período
    if tipificacion:
        tipis_disponibles = [r[0] for r in rows_tipis if r[0]]
        tipis_expandidas = _expandir_tipificaciones(tipificacion, tipis_disponibles)
        where_parts.append(f"d.Tipificación IN ({_escapar_sql_in(tipis_expandidas)})")

    # 2. Conteo por operador con supervisor (CTE previo para no llamar a la función en cada fila)
    where_parts_sin_operador = [p for p in where_parts if not p.startswith("d.loginId IN")]
    where_sql_sin_operador = " AND ".join(where_parts_sin_operador)

    q_operadores = text(f"""
        WITH OperadoresCTE AS (
            SELECT 
                d.loginId AS login_id,
                COUNT(*) AS cantidad
            {from_sql}
            WHERE {where_sql_sin_operador}
              AND d.loginId IS NOT NULL AND d.loginId <> ''
            GROUP BY d.loginId
        )
        SELECT 
            op.login_id,
            op.cantidad,
            ag.Nombre,
            ag.Apellido,
            ag.Legajo,
            e.nombre AS SupNombre,
            e.apellido AS SupApellido
        FROM OperadoresCTE op
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(op.login_id, :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag
        LEFT JOIN equipos e ON e.id = ag.EquipoID
        ORDER BY op.cantidad DESC
    """)
    rows_ops = conn.execute(q_operadores, params).fetchall()

    operadores = []
    supervisores_set = set()
    for r in rows_ops:
        login_val = str(r.login_id).strip()
        nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else login_val
        sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
        if sup_nombre != "Sin asignar":
            supervisores_set.add(sup_nombre)
        operadores.append({
            "login_id": login_val,
            "nombre": nombre_completo,
            "legajo": r.Legajo or "",
            "supervisor": sup_nombre,
            "cantidad": r.cantidad
        })

    # 3. Total general y desglose por sentido en una única consulta
    where_sql = " AND ".join(where_parts)
    q_total_y_sentido = text(f"""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN d.Sentido = 'Entrante' THEN 1 ELSE 0 END) AS cnt_entrante,
            SUM(CASE WHEN d.Sentido = 'Saliente' THEN 1 ELSE 0 END) AS cnt_saliente,
            SUM(CASE WHEN d.Sentido = 'Interna Saliente' THEN 1 ELSE 0 END) AS cnt_interno
        {from_sql}
        WHERE {where_sql}
    """)
    row_ts = conn.execute(q_total_y_sentido, params).fetchone()
    total_audios = row_ts.total if row_ts and row_ts.total else 0
    sentido_counts = {
        "Entrante": row_ts.cnt_entrante if row_ts and row_ts.cnt_entrante else 0,
        "Saliente": row_ts.cnt_saliente if row_ts and row_ts.cnt_saliente else 0,
        "Interno": row_ts.cnt_interno if row_ts and row_ts.cnt_interno else 0,
    }

    return {
        "total_audios": total_audios,
        "tipificaciones": arbol_tipis,
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": sentido_counts,
        "es_arbol": True
    }


def _consultar_cxone(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool
) -> Dict[str, Any]:
    q_skills = text("SELECT Nombre FROM calidad.Skills WHERE CampanaID = :campana_id AND IsActive = 1")
    skills_rows = conn.execute(q_skills, {"campana_id": campana_id}).fetchall()
    skill_nombres = [r[0] for r in skills_rows if r[0]]
    if not skill_nombres:
        return {
            "total_audios": 0,
            "tipificaciones": [],
            "operadores": [],
            "supervisores": [],
            "sentido_counts": {"Entrante": 0, "Saliente": 0, "Interno": 0},
            "es_arbol": False
        }

    where_parts = [
        f"g.skillName IN ({_escapar_sql_in(skill_nombres)})",
        "ISNULL(g.segmentContactStartTime, g.endTime) >= :fd_dt",
        "ISNULL(g.segmentContactStartTime, g.endTime) <= :fh_dt"
    ]
    params = {
        "fd_dt": f"{fecha_desde} 00:00:00",
        "fh_dt": f"{fecha_hasta} 23:59:59",
        "empresa_id": empresa_id
    }

    if not reauditar:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = g.segmentId)"
        )
    if duracion_min:
        where_parts.append(f"g.duration >= {int(duracion_min)}")
    if duracion_max:
        where_parts.append(f"g.duration <= {int(duracion_max)}")
    if sentido:
        sentido_map = {"Entrante": "IN_BOUND", "Saliente": "OUT_BOUND", "Interno": "INTERNAL"}
        dir_mapped = [sentido_map[s] for s in sentido if s in sentido_map]
        if dir_mapped:
            where_parts.append(f"g.directionType IN ({_escapar_sql_in(dir_mapped)})")
    if loginid:
        where_parts.append(f"CAST(g.agentIds AS VARCHAR(50)) IN ({_escapar_sql_in(loginid)})")

    from_sql = "FROM [Acme].[ALARMIX].[Grabaciones] g"

    where_sql_sin_tipi = " AND ".join(where_parts)

    q_tipis = text(f"""
        SELECT g.callTagging, COUNT(*) as cantidad
        {from_sql}
        WHERE {where_sql_sin_tipi}
          AND g.callTagging IS NOT NULL AND g.callTagging <> ''
        GROUP BY g.callTagging
        ORDER BY cantidad DESC
    """)
    rows_tipis = conn.execute(q_tipis, params).fetchall()
    tipis_lista = [{"label": r[0], "value": r[0], "cantidad": r[1]} for r in rows_tipis]

    if tipificacion:
        tipis_disponibles = [r[0] for r in rows_tipis if r[0]]
        tipis_expandidas = _expandir_tipificaciones(tipificacion, tipis_disponibles)
        where_parts.append(f"g.callTagging IN ({_escapar_sql_in(tipis_expandidas)})")

    where_parts_sin_operador = [p for p in where_parts if not p.startswith("CAST(g.agentIds")]
    where_sql_sin_operador = " AND ".join(where_parts_sin_operador)

    q_operadores = text(f"""
        WITH OperadoresCTE AS (
            SELECT 
                CAST(g.agentIds AS VARCHAR(50)) AS login_id,
                MAX(g.agentName_sort) AS agent_name,
                COUNT(*) AS cantidad
            {from_sql}
            WHERE {where_sql_sin_operador}
              AND g.agentIds IS NOT NULL
            GROUP BY CAST(g.agentIds AS VARCHAR(50))
        )
        SELECT 
            op.login_id,
            op.agent_name,
            op.cantidad,
            COALESCE(ag.Nombre, n.nombre) AS Nombre,
            COALESCE(ag.Apellido, n.apellido) AS Apellido,
            COALESCE(ag.Legajo, n.legajo) AS Legajo,
            COALESCE(e1.nombre, e2.nombre) AS SupNombre,
            COALESCE(e1.apellido, e2.apellido) AS SupApellido
        FROM OperadoresCTE op
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(op.login_id, :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag
        LEFT JOIN equipos e1 ON e1.id = ag.EquipoID
        OUTER APPLY (
            SELECT TOP 1 n2.id, n2.nombre, n2.apellido, n2.legajo, o2.equipo_id
            FROM nomina n2
            JOIN operadores o2 ON o2.legajo_id = n2.id
            JOIN dbo.campanas cmp ON cmp.id = o2.campana_id
            WHERE cmp.cliente = 'ALARMIX'
              AND o2.estado = 1
              AND GETDATE() >= o2.fecha_desde
              AND (o2.fecha_hasta IS NULL OR GETDATE() < o2.fecha_hasta)
              AND (
                  op.agent_name LIKE '%' + n2.apellido + '%'
                  AND (
                      op.agent_name LIKE '%' + n2.nombre + '%'
                      OR n2.nombre LIKE '%' + op.agent_name + '%'
                      OR EXISTS (
                          SELECT 1 FROM STRING_SPLIT(n2.nombre, ' ') w 
                          WHERE LEN(w.value) >= 3 AND op.agent_name LIKE '%' + w.value + '%'
                      )
                  )
              )
            ORDER BY o2.fecha_desde DESC
        ) n
        LEFT JOIN equipos e2 ON e2.id = n.equipo_id
        ORDER BY op.cantidad DESC
    """)
    rows_ops = conn.execute(q_operadores, params).fetchall()

    operadores = []
    supervisores_set = set()
    for r in rows_ops:
        login_val = str(r.login_id).strip()
        nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else (r.agent_name or login_val)
        sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
        if sup_nombre != "Sin asignar":
            supervisores_set.add(sup_nombre)
        operadores.append({
            "login_id": login_val,
            "nombre": nombre_completo,
            "legajo": r.Legajo or "",
            "supervisor": sup_nombre,
            "cantidad": r.cantidad
        })

    where_sql = " AND ".join(where_parts)
    q_total_y_sentido = text(f"""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN g.directionType = 'IN_BOUND' THEN 1 ELSE 0 END) AS cnt_entrante,
            SUM(CASE WHEN g.directionType = 'OUT_BOUND' THEN 1 ELSE 0 END) AS cnt_saliente,
            SUM(CASE WHEN g.directionType = 'INTERNAL' THEN 1 ELSE 0 END) AS cnt_interno
        {from_sql}
        WHERE {where_sql}
    """)
    row_ts = conn.execute(q_total_y_sentido, params).fetchone()
    total_audios = row_ts.total if row_ts and row_ts.total else 0
    sentido_counts = {
        "Entrante": row_ts.cnt_entrante if row_ts and row_ts.cnt_entrante else 0,
        "Saliente": row_ts.cnt_saliente if row_ts and row_ts.cnt_saliente else 0,
        "Interno": row_ts.cnt_interno if row_ts and row_ts.cnt_interno else 0,
    }

    return {
        "total_audios": total_audios,
        "tipificaciones": tipis_lista,
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": sentido_counts,
        "es_arbol": False
    }


def _consultar_genesys(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool
) -> Dict[str, Any]:
    q_skills = text("SELECT Nombre FROM calidad.Skills WHERE CampanaID = :campana_id AND IsActive = 1")
    skills_rows = conn.execute(q_skills, {"campana_id": campana_id}).fetchall()
    skill_nombres = [r[0] for r in skills_rows if r[0]]
    if not skill_nombres:
        return {
            "total_audios": 0,
            "tipificaciones": [],
            "operadores": [],
            "supervisores": [],
            "sentido_counts": {"Entrante": 0, "Saliente": 0, "Interno": 0},
            "es_arbol": True
        }

    where_parts = [
        f"i.[Cola] IN ({_escapar_sql_in(skill_nombres)})",
        "i.[Canal] = 'voice' AND i.[Grabada] = 1 AND i.[SegundosHablados] > 0",
        "(i.[Operador] LIKE N'ACME - %' OR i.[Email] LIKE N'%@consultores.benefix.example')",
        "i.[Fecha] >= :fd_d AND i.[Fecha] <= :fh_d"
    ]
    params = {
        "fd_d": fecha_desde,
        "fh_d": fecha_hasta,
        "empresa_id": empresa_id
    }

    if not reauditar:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = i.ConversationId)"
        )
    if duracion_min:
        where_parts.append(f"i.[SegundosHablados] >= {int(duracion_min)}")
    if duracion_max:
        where_parts.append(f"i.[SegundosHablados] <= {int(duracion_max)}")
    if sentido:
        sentidos_validos = [s for s in sentido if s in ('Entrante', 'Saliente')]
        if sentidos_validos:
            where_parts.append(f"i.[Sentido] IN ({_escapar_sql_in(sentidos_validos)})")
        else:
            where_parts.append("1 = 0")
    if loginid:
        where_parts.append(f"i.[Operador] IN ({_escapar_sql_in(loginid)})")

    from_sql = "FROM [Acme].[Benefix].[Interacciones] i"

    where_sql_sin_tipi = " AND ".join(where_parts)

    q_tipis = text(f"""
        SELECT i.[Tipificacion], COUNT(*) as cantidad
        {from_sql}
        WHERE {where_sql_sin_tipi}
          AND i.[Tipificacion] IS NOT NULL AND i.[Tipificacion] <> ''
        GROUP BY i.[Tipificacion]
        ORDER BY cantidad DESC
    """)
    rows_tipis = conn.execute(q_tipis, params).fetchall()
    tipis_lista = [{"tipificacion": r[0], "cantidad": r[1]} for r in rows_tipis]
    arbol_tipis = _construir_arbol_tipificaciones(tipis_lista, [' - ', '- '])

    if tipificacion:
        tipis_disponibles = [r[0] for r in rows_tipis if r[0]]
        tipis_expandidas = _expandir_tipificaciones(tipificacion, tipis_disponibles)
        where_parts.append(f"i.[Tipificacion] IN ({_escapar_sql_in(tipis_expandidas)})")

    where_parts_sin_operador = [p for p in where_parts if not p.startswith("i.[Operador] IN")]
    where_sql_sin_operador = " AND ".join(where_parts_sin_operador)

    q_operadores = text(f"""
        WITH OperadoresCTE AS (
            SELECT 
                i.[Operador] AS login_id,
                COUNT(*) AS cantidad
            {from_sql}
            WHERE {where_sql_sin_operador}
              AND i.[Operador] IS NOT NULL
            GROUP BY i.[Operador]
        )
        SELECT 
            op.login_id,
            op.cantidad,
            ag.Nombre,
            ag.Apellido,
            ag.Legajo,
            e.nombre AS SupNombre,
            e.apellido AS SupApellido
        FROM OperadoresCTE op
        OUTER APPLY (
            SELECT ag.*
            FROM calidad.fn_ResolverOperadorAuditoria(op.login_id, :empresa_id, GETDATE()) ag
            JOIN operadores o ON o.legajo_id = ag.NominaID
            JOIN dbo.campanas cmp ON cmp.id = o.campana_id
            WHERE cmp.cliente = 'Benefix'
              AND o.estado = 1
              AND GETDATE() >= o.fecha_desde
              AND (o.fecha_hasta IS NULL OR GETDATE() < o.fecha_hasta)
        ) ag
        LEFT JOIN equipos e ON e.id = ag.EquipoID
        ORDER BY op.cantidad DESC
    """)
    rows_ops = conn.execute(q_operadores, params).fetchall()

    operadores = []
    supervisores_set = set()
    for r in rows_ops:
        login_val = str(r.login_id).strip()
        nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else login_val.replace("ACME - ", "").strip()
        sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
        if sup_nombre != "Sin asignar":
            supervisores_set.add(sup_nombre)
        operadores.append({
            "login_id": login_val,
            "nombre": nombre_completo,
            "legajo": r.Legajo or "",
            "supervisor": sup_nombre,
            "cantidad": r.cantidad
        })

    where_sql = " AND ".join(where_parts)
    q_total_y_sentido = text(f"""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN i.[Sentido] = 'Entrante' THEN 1 ELSE 0 END) AS cnt_entrante,
            SUM(CASE WHEN i.[Sentido] = 'Saliente' THEN 1 ELSE 0 END) AS cnt_saliente
        {from_sql}
        WHERE {where_sql}
    """)
    row_ts = conn.execute(q_total_y_sentido, params).fetchone()
    total_audios = row_ts.total if row_ts and row_ts.total else 0
    sentido_counts = {
        "Entrante": row_ts.cnt_entrante if row_ts and row_ts.cnt_entrante else 0,
        "Saliente": row_ts.cnt_saliente if row_ts and row_ts.cnt_saliente else 0,
        "Interno": 0
    }

    return {
        "total_audios": total_audios,
        "tipificaciones": arbol_tipis,
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": sentido_counts,
        "es_arbol": True
    }


def _consultar_wize(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool
) -> Dict[str, Any]:
    where_parts = [
        "d.agente_usuario LIKE '% Acme'",
        "d.url_grabacion IS NOT NULL",
        "d.fecha_inicio >= :fd_dt AND d.fecha_inicio <= :fh_dt"
    ]
    params = {
        "fd_dt": f"{fecha_desde} 00:00:00",
        "fh_dt": f"{fecha_hasta} 23:59:59",
        "empresa_id": empresa_id
    }

    if not reauditar:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = CAST(d.[caso_id] AS VARCHAR(50)))"
        )
    if duracion_min:
        where_parts.append(f"d.[duracion_total] >= {int(duracion_min)}")
    if duracion_max:
        where_parts.append(f"d.[duracion_total] <= {int(duracion_max)}")
    if sentido:
        sentidos_map = {'Entrante': '1', 'Saliente': '0'}
        s_vals = [sentidos_map[s] for s in sentido if s in sentidos_map]
        if s_vals:
            where_parts.append(f"d.[es_entrante] IN ({','.join(s_vals)})")
        else:
            where_parts.append("1 = 0")
    if loginid:
        where_parts.append(f"d.agente_usuario IN ({_escapar_sql_in(loginid)})")

    from_sql = "FROM [Vitalis_Llamadas] d"

    where_sql_sin_tipi = " AND ".join(where_parts)

    q_tipis = text(f"""
        SELECT d.[subtipo_interaccion], COUNT(*) as cantidad
        {from_sql}
        WHERE {where_sql_sin_tipi}
          AND d.[subtipo_interaccion] IS NOT NULL AND d.[subtipo_interaccion] <> ''
        GROUP BY d.[subtipo_interaccion]
        ORDER BY cantidad DESC
    """)
    rows_tipis = conn.execute(q_tipis, params).fetchall()
    tipis_lista = [{"tipificacion": r[0], "cantidad": r[1]} for r in rows_tipis]
    arbol_tipis = _construir_arbol_tipificaciones(tipis_lista, [' -> ', ' - '])

    if tipificacion:
        tipis_disponibles = [r[0] for r in rows_tipis if r[0]]
        tipis_expandidas = _expandir_tipificaciones(tipificacion, tipis_disponibles)
        where_parts.append(f"d.[subtipo_interaccion] IN ({_escapar_sql_in(tipis_expandidas)})")

    where_parts_sin_operador = [p for p in where_parts if not p.startswith("d.agente_usuario IN")]
    where_sql_sin_operador = " AND ".join(where_parts_sin_operador)

    q_operadores = text(f"""
        WITH OperadoresCTE AS (
            SELECT 
                d.agente_usuario AS login_id,
                COUNT(*) AS cantidad
            {from_sql}
            WHERE {where_sql_sin_operador}
              AND d.agente_usuario IS NOT NULL
            GROUP BY d.agente_usuario
        )
        SELECT 
            op.login_id,
            op.cantidad,
            COALESCE(ag1.Nombre, ag2.Nombre) AS Nombre,
            COALESCE(ag1.Apellido, ag2.Apellido) AS Apellido,
            COALESCE(ag1.Legajo, ag2.Legajo) AS Legajo,
            COALESCE(e1.nombre, e2.nombre) AS SupNombre,
            COALESCE(e1.apellido, e2.apellido) AS SupApellido
        FROM OperadoresCTE op
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(op.login_id, :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag1
        LEFT JOIN equipos e1 ON e1.id = ag1.EquipoID
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(REPLACE(op.login_id, ' Acme', ''), :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag2
        LEFT JOIN equipos e2 ON e2.id = ag2.EquipoID
        ORDER BY op.cantidad DESC
    """)
    rows_ops = conn.execute(q_operadores, params).fetchall()

    operadores = []
    supervisores_set = set()
    for r in rows_ops:
        login_val = str(r.login_id).strip()
        login_limpio = login_val.replace(" Acme", "").replace(" ACME", "").strip()
        nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else login_limpio
        sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
        if sup_nombre != "Sin asignar":
            supervisores_set.add(sup_nombre)
        operadores.append({
            "login_id": login_val,
            "nombre": nombre_completo,
            "legajo": r.Legajo or "",
            "supervisor": sup_nombre,
            "cantidad": r.cantidad
        })

    where_sql = " AND ".join(where_parts)
    q_total_y_sentido = text(f"""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN d.[es_entrante] = 1 THEN 1 ELSE 0 END) AS cnt_entrante,
            SUM(CASE WHEN d.[es_entrante] = 0 THEN 1 ELSE 0 END) AS cnt_saliente
        {from_sql}
        WHERE {where_sql}
    """)
    row_ts = conn.execute(q_total_y_sentido, params).fetchone()
    total_audios = row_ts.total if row_ts and row_ts.total else 0
    sentido_counts = {
        "Entrante": row_ts.cnt_entrante if row_ts and row_ts.cnt_entrante else 0,
        "Saliente": row_ts.cnt_saliente if row_ts and row_ts.cnt_saliente else 0,
        "Interno": 0
    }

    return {
        "total_audios": total_audios,
        "tipificaciones": arbol_tipis,
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": sentido_counts,
        "es_arbol": True
    }


def _consultar_hermes(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool
) -> Dict[str, Any]:
    where_parts = [
        "i.[CallLocalTime] >= :fd_dt AND i.[CallLocalTime] <= :fh_dt"
    ]
    params = {
        "fd_dt": f"{fecha_desde} 00:00:00",
        "fh_dt": f"{fecha_hasta} 23:59:59",
        "empresa_id": empresa_id
    }

    if not reauditar:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = CAST(i.[ID] AS VARCHAR(50)))"
        )
    if duracion_min:
        where_parts.append(f"i.[Duration] >= {int(duracion_min)}")
    if duracion_max:
        where_parts.append(f"i.[Duration] <= {int(duracion_max)}")
    if loginid:
        where_parts.append(f"CAST(i.[Rec_AgentId] AS VARCHAR(50)) IN ({_escapar_sql_in(loginid)})")

    where_sql = " AND ".join(where_parts)
    from_sql = "FROM [Acme].[Hidra].[interacciones] i"

    where_parts_sin_operador = [p for p in where_parts if not p.startswith("CAST(i.[Rec_AgentId]")]
    where_sql_sin_operador = " AND ".join(where_parts_sin_operador)

    q_operadores = text(f"""
        WITH OperadoresCTE AS (
            SELECT 
                CAST(i.[Rec_AgentId] AS VARCHAR(50)) AS login_id,
                COUNT(*) AS cantidad
            {from_sql}
            WHERE {where_sql_sin_operador}
              AND i.[Rec_AgentId] IS NOT NULL
            GROUP BY i.[Rec_AgentId]
        )
        SELECT 
            op.login_id,
            op.cantidad,
            ag.Nombre,
            ag.Apellido,
            ag.Legajo,
            e.nombre AS SupNombre,
            e.apellido AS SupApellido
        FROM OperadoresCTE op
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(op.login_id, :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag
        LEFT JOIN equipos e ON e.id = ag.EquipoID
        ORDER BY op.cantidad DESC
    """)
    rows_ops = conn.execute(q_operadores, params).fetchall()

    operadores = []
    supervisores_set = set()
    for r in rows_ops:
        login_val = str(r.login_id).strip()
        nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else login_val
        sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
        if sup_nombre != "Sin asignar":
            supervisores_set.add(sup_nombre)
        operadores.append({
            "login_id": login_val,
            "nombre": nombre_completo,
            "legajo": r.Legajo or "",
            "supervisor": sup_nombre,
            "cantidad": r.cantidad
        })

    q_total = text(f"""
        SELECT COUNT(*) AS total
        {from_sql}
        WHERE {where_sql}
    """)
    total_audios = conn.execute(q_total, params).scalar() or 0

    return {
        "total_audios": total_audios,
        "tipificaciones": [],
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": {"Entrante": total_audios, "Saliente": 0, "Interno": 0},
        "es_arbol": False
    }


def _consultar_orion(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool
) -> Dict[str, Any]:
    from app.managers import plantillas_manager_instance
    tree = plantillas_manager_instance.listar_tipificaciones(campana_id)
    tree_list = tree if isinstance(tree, list) else []

    q_skills = text("SELECT Nombre FROM calidad.Skills WHERE CampanaID = :campana_id AND IsActive = 1")
    skills_rows = conn.execute(q_skills, {"campana_id": campana_id}).fetchall()
    skill_nombres = [r[0] for r in skills_rows if r[0]]
    if not skill_nombres:
        return {
            "total_audios": 0,
            "tipificaciones": tree_list,
            "operadores": [],
            "supervisores": [],
            "sentido_counts": {"Entrante": 0, "Saliente": 0, "Interno": 0},
            "es_arbol": True
        }

    def _sql_str_remote(v: str) -> str:
        return "''" + str(v).replace("'", "''''") + "''"

    skills_in = ",".join(_sql_str_remote(s) for s in skill_nombres)

    filtros_remotos = [
        f"e.Fecha >= ''{fecha_desde}'' AND e.Fecha < DATEADD(DAY, 1, ''{fecha_hasta}'')",
        "e.Grabada = 1",
        "e.TiempoHablado > 0",
        f"h.Nombre IN ({skills_in})"
    ]
    if duracion_min:
        filtros_remotos.append(f"e.TiempoHablado >= {int(duracion_min)}")
    if duracion_max:
        filtros_remotos.append(f"e.TiempoHablado <= {int(duracion_max)}")
    if sentido:
        direccion_base = {'Entrante': ['Entrante'], 'Saliente': ['Saliente', 'Discador']}
        dirs = [d for s in sentido for d in direccion_base.get(s, [s])]
        dirs_in = ",".join(_sql_str_remote(d) for d in dirs)
        filtros_remotos.append(f"tl.Descripcion IN ({dirs_in})")
    if loginid:
        logins_in = ",".join(_sql_str_remote(l) for l in loginid)
        filtros_remotos.append(f"e.Agente IN ({logins_in})")
    if tipificacion:
        tipis_in = ",".join(_sql_str_remote(t) for t in tipificacion)
        filtros_remotos.append(
            f"LTRIM(RTRIM(REPLACE(REPLACE(sub.Descripcion, CHAR(13), ''''), CHAR(10), ''''))) IN ({tipis_in})"
        )

    rem_where = " AND ".join(filtros_remotos)
    filtros_rem_sin_op = [f for f in filtros_remotos if not f.startswith("e.Agente IN")]
    rem_where_sin_op = " AND ".join(filtros_rem_sin_op)

    q_remota_ops = f"""
        SELECT 
            CAST(e.Agente AS VARCHAR(50)) AS Agente,
            a.Nombre_del_agente,
            COUNT(*) AS cantidad
        FROM ContactCenter.dbo.EstadPorLlamada e
        JOIN ContactCenter.dbo.Agentes a ON a.Legajo = e.Agente
        LEFT JOIN ContactCenter.dbo.TiposLlamada tl ON tl.Tipo = e.Tipo
        LEFT JOIN ContactCenter.dbo.DatosPorLlamada dps ON dps.Call_Id = e.ID_Llamada AND dps.Clave = ''CODSUBCAT''
        LEFT JOIN ContactCenter.dbo.SubCategorias sub ON sub.CodSubcategoria = COALESCE(TRY_CAST(dps.Valor AS INT), NULLIF(e.Codificacion, 0))
        JOIN ContactCenter.dbo.HabilidadesPorAgenteLog hl ON hl.AgenteID = e.Agente
            AND hl.FechaInicio <= e.Fecha
            AND (hl.FechaFin IS NULL OR hl.FechaFin >= e.Fecha)
        JOIN ContactCenter.dbo.Habilidades h ON h.ID = hl.HabilidadID
        WHERE {rem_where_sin_op}
        GROUP BY e.Agente, a.Nombre_del_agente
    """

    sql_ops = f"""
        WITH OpsRemotos AS (
            SELECT * FROM OPENQUERY(ORION_LINK, '{q_remota_ops}')
        )
        SELECT 
            op.Agente AS login_id,
            op.Nombre_del_agente,
            op.cantidad,
            ag.Nombre,
            ag.Apellido,
            ag.Legajo,
            e.nombre AS SupNombre,
            e.apellido AS SupApellido
        FROM OpsRemotos op
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(op.Agente, :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag
        LEFT JOIN equipos e ON e.id = ag.EquipoID
        ORDER BY op.cantidad DESC
    """

    operadores = []
    supervisores_set = set()
    try:
        rows_ops = conn.execute(text(sql_ops), {"empresa_id": empresa_id}).fetchall()
        for r in rows_ops:
            login_val = str(r.login_id).strip()
            nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else (r.Nombre_del_agente or login_val)
            sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
            if sup_nombre != "Sin asignar":
                supervisores_set.add(sup_nombre)
            operadores.append({
                "login_id": login_val,
                "nombre": nombre_completo,
                "legajo": r.Legajo or "",
                "supervisor": sup_nombre,
                "cantidad": r.cantidad
            })
    except Exception as e:
        logger.warning(f"Error consultando operadores Orion: {e}")

    q_remota_total = f"""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN tl.Descripcion = ''Entrante'' THEN 1 ELSE 0 END) AS cnt_entrante,
            SUM(CASE WHEN tl.Descripcion IN (''Saliente'', ''Discador'') THEN 1 ELSE 0 END) AS cnt_saliente
        FROM ContactCenter.dbo.EstadPorLlamada e
        LEFT JOIN ContactCenter.dbo.TiposLlamada tl ON tl.Tipo = e.Tipo
        LEFT JOIN ContactCenter.dbo.DatosPorLlamada dps ON dps.Call_Id = e.ID_Llamada AND dps.Clave = ''CODSUBCAT''
        LEFT JOIN ContactCenter.dbo.SubCategorias sub ON sub.CodSubcategoria = COALESCE(TRY_CAST(dps.Valor AS INT), NULLIF(e.Codificacion, 0))
        JOIN ContactCenter.dbo.HabilidadesPorAgenteLog hl ON hl.AgenteID = e.Agente
            AND hl.FechaInicio <= e.Fecha
            AND (hl.FechaFin IS NULL OR hl.FechaFin >= e.Fecha)
        JOIN ContactCenter.dbo.Habilidades h ON h.ID = hl.HabilidadID
        WHERE {rem_where}
    """

    total_audios = 0
    sentido_counts = {"Entrante": 0, "Saliente": 0, "Interno": 0}
    try:
        row_tot = conn.execute(text(f"SELECT * FROM OPENQUERY(ORION_LINK, '{q_remota_total}')")).fetchone()
        if row_tot:
            total_audios = row_tot[0] or 0
            sentido_counts["Entrante"] = row_tot[1] or 0
            sentido_counts["Saliente"] = row_tot[2] or 0
    except Exception as e:
        logger.warning(f"Error consultando total Orion: {e}")

    return {
        "total_audios": total_audios,
        "tipificaciones": tree_list,
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": sentido_counts,
        "es_arbol": True
    }


def _consultar_astervoip(
    conn: Connection,
    campana_id: int,
    empresa_id: int,
    fecha_desde: str,
    fecha_hasta: str,
    duracion_min: Optional[int],
    duracion_max: Optional[int],
    sentido: List[str],
    loginid: List[str],
    tipificacion: List[str],
    reauditar: bool
) -> Dict[str, Any]:
    q_skills = text("SELECT Nombre FROM calidad.Skills WHERE CampanaID = :campana_id AND IsActive = 1")
    skills_rows = conn.execute(q_skills, {"campana_id": campana_id}).fetchall()
    skill_nombres = [r[0] for r in skills_rows if r[0]]
    if not skill_nombres:
        return {
            "total_audios": 0,
            "tipificaciones": [],
            "operadores": [],
            "supervisores": [],
            "sentido_counts": {"Entrante": 0, "Saliente": 0, "Interno": 0},
            "es_arbol": True
        }

    where_parts = [
        f"gr.[Cola Entrante] IN ({_escapar_sql_in(skill_nombres)})",
        "CAST(gr.[Fecha/Hora] AS DATE) >= :fd_d AND CAST(gr.[Fecha/Hora] AS DATE) <= :fh_d",
        "gr.[Duración] > 0"
    ]
    params = {
        "fd_d": fecha_desde,
        "fh_d": fecha_hasta,
        "fd_dt": f"{fecha_desde} 00:00:00",
        "fh_dt": f"{fecha_hasta} 23:59:59",
        "empresa_id": empresa_id
    }

    if not reauditar:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a "
            "WHERE a.[IdAplicativo] = CONCAT(gr.[Origen], '_', gr.[Destino], '_', FORMAT(gr.[Fecha/Hora], 'yyyyMMddHHmmss')))"
        )
    if duracion_min:
        where_parts.append(f"gr.[Duración] >= {int(duracion_min)}")
    if duracion_max:
        where_parts.append(f"gr.[Duración] <= {int(duracion_max)}")
    if loginid:
        where_parts.append(f"gr.[Agente que Atendió] IN ({_escapar_sql_in(loginid)})")

    from_sql = "FROM [Acme].[Farmalux].[Grabaciones] gr"

    # Tipificaciones desde SalesForce_casos
    q_tipis = text("""
        SELECT 
            CASE WHEN sf.Submotivo IS NOT NULL AND sf.Submotivo <> '' 
                 THEN sf.Motivo + ' - ' + sf.Submotivo 
                 ELSE sf.Motivo END AS tipificacion,
            COUNT(*) AS cantidad
        FROM [Acme].[Farmalux].[SalesForce_casos] sf
        WHERE sf.[Última fecha/hora de modificación de caso] >= :fd_dt
          AND sf.[Última fecha/hora de modificación de caso] <= :fh_dt
          AND sf.Motivo IS NOT NULL AND sf.Motivo <> ''
        GROUP BY sf.Motivo, sf.Submotivo
        ORDER BY cantidad DESC
    """)
    rows_tipis = conn.execute(q_tipis, params).fetchall()
    tipis_lista = [{"tipificacion": r[0], "cantidad": r[1]} for r in rows_tipis]
    arbol_tipis = _construir_arbol_tipificaciones(tipis_lista, [' - '])

    # Operadores con supervisor
    where_parts_sin_operador = [p for p in where_parts if not p.startswith("gr.[Agente que Atendió]")]
    where_sql_sin_operador = " AND ".join(where_parts_sin_operador)

    q_operadores = text(f"""
        WITH OperadoresCTE AS (
            SELECT 
                gr.[Agente que Atendió] AS login_id,
                COUNT(*) AS cantidad
            {from_sql}
            WHERE {where_sql_sin_operador}
              AND gr.[Agente que Atendió] IS NOT NULL AND gr.[Agente que Atendió] <> ''
            GROUP BY gr.[Agente que Atendió]
        )
        SELECT 
            op.login_id,
            op.cantidad,
            ag.Nombre,
            ag.Apellido,
            ag.Legajo,
            e.nombre AS SupNombre,
            e.apellido AS SupApellido
        FROM OperadoresCTE op
        OUTER APPLY (
            SELECT *
            FROM calidad.fn_ResolverOperadorAuditoria(op.login_id, :empresa_id, GETDATE())
            WHERE Criterio = 1
        ) ag
        LEFT JOIN equipos e ON e.id = ag.EquipoID
        ORDER BY op.cantidad DESC
    """)
    rows_ops = conn.execute(q_operadores, params).fetchall()

    operadores = []
    supervisores_set = set()
    for r in rows_ops:
        login_val = str(r.login_id).strip()
        nombre_completo = f"{r.Apellido}, {r.Nombre}".strip(" ,") if r.Nombre or r.Apellido else login_val
        sup_nombre = f"{r.SupApellido}, {r.SupNombre}".strip(" ,") if r.SupNombre or r.SupApellido else "Sin asignar"
        if sup_nombre != "Sin asignar":
            supervisores_set.add(sup_nombre)
        operadores.append({
            "login_id": login_val,
            "nombre": nombre_completo,
            "legajo": r.Legajo or "",
            "supervisor": sup_nombre,
            "cantidad": r.cantidad
        })

    where_sql = " AND ".join(where_parts)
    q_total = text(f"""
        SELECT COUNT(*) AS total
        {from_sql}
        WHERE {where_sql}
    """)
    total_audios = conn.execute(q_total, params).scalar() or 0

    return {
        "total_audios": total_audios,
        "tipificaciones": arbol_tipis,
        "operadores": operadores,
        "supervisores": sorted(list(supervisores_set)),
        "sentido_counts": {"Entrante": total_audios, "Saliente": 0, "Interno": 0},
        "es_arbol": True
    }
