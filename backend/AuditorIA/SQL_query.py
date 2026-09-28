import pandas as pd
import re

from typing import List, Union
from datetime import datetime

from app.config import settings
from cachetools import cached, TTLCache

from AuditorIA.avisos import AvisoUsuarioError


class AuditLimitExceededError(AvisoUsuarioError):
    """Se activó por_operador o por_tipificacion y el muestreo superó el límite de 200 registros."""
    def __init__(self, cantidad_obtenida: int, limite: int = 200):
        self.cantidad_obtenida = cantidad_obtenida
        self.limite = limite
        super().__init__(
            f"Se solicitaron {cantidad_obtenida} auditorías, lo cual supera el límite de {limite}. "
            f"Reduzca la cantidad por muestreo o ajuste los filtros (operadores/tipificaciones)."
        )

from descargar_drive import descarga_drive_memoria
from sqlalchemy import Engine


def limpiar_y_estructurar_Odonto_plus(df) -> List[pd.DataFrame]:
    """
    Busca la fila que contiene los encabezados reales.
    Retorna una lista con dos DataFrames: [df_columna_D_previa, df_cuadro_principal]
    """
    indice_encabezado = -1
    
    for i, row in df.iterrows():
        fila_texto = " ".join([str(val) for val in row.values if pd.notna(val)]).upper()
        if "ODONTÓLOGO" in fila_texto and "HORARIO" in fila_texto:
            indice_encabezado = i
            break
    
    if indice_encabezado != -1:
        # Extraer la Columna D (índice 3) antes del cuadro
        if 3 in df.columns:
            df_col_d = df.iloc[:indice_encabezado, [3]].copy()
            df_col_d.columns = ['Datos_Previos_Col_D'] 
            df_col_d = df_col_d.dropna(how='all').reset_index(drop=True)
        else:
            df_col_d = pd.DataFrame(columns=['Datos_Previos_Col_D'])

        # Extraer el cuadro principal
        df_principal = df.copy()
        df_principal.columns = df_principal.iloc[indice_encabezado]
        df_principal = df_principal.iloc[indice_encabezado + 1:].reset_index(drop=True)
        
        # Limpieza extra
        if 'ODONTÓLOGO' in df_principal.columns:
            df_principal = df_principal[df_principal['ODONTÓLOGO'].notna()] 
            df_principal = df_principal[~df_principal['ODONTÓLOGO'].astype(str).str.contains("REEMPLAZOS", case=False, na=False)]
            
        return [df_col_d, df_principal]
    else:
        return [pd.DataFrame(), df]

@cached(cache=TTLCache(maxsize=1, ttl=3600))
def diccionario_dental_clinicas() -> dict[int,list[pd.DataFrame]]:
    # 1. Descargamos el archivo directamente a la memoria RAM
    archivo_en_memoria = descarga_drive_memoria(settings.SHEET_ID_DENTAL_CRONOGRAMA_DE_ATENCION)
    
    # Pandas puede leer objetos BytesIO sin problema, igual que si fuera un archivo .xlsx
    dfs_crudos = pd.read_excel(archivo_en_memoria, sheet_name=None, header=None)
    
    diccionario_clinicas = {}

    for nombre_hoja, df in dfs_crudos.items():
        if df.empty:
            continue
            
        # Extraer el ID
        primera_celda = str(df.iloc[0, 0])
        match_id = re.search(r'(\d+)', primera_celda)
        
        if match_id:
            key_dict = match_id.group(1) 
            key_dict = int(key_dict)
        else:
            continue 

        # La función devuelve [df_col_d, df_principal]
        datos_extraidos = limpiar_y_estructurar_Odonto_plus(df)
        
        # Guardamos en el diccionario
        diccionario_clinicas[key_dict] = datos_extraidos
    
    return diccionario_clinicas

def _filtro_comentario_like(columna: str, comentario: Union[list[str], None]) -> str:
    """Devuelve un predicado SQL ' AND (col LIKE '%v1%' OR col LIKE '%v2%' ...)' para
    el filtro de comentario del frontend: varios valores combinados con OR + LIKE
    (contiene, case-insensitive por la collation default). Comillas simples escapadas.
    Devuelve '' si no hay valores. Sirve también dentro de OPENQUERY: las comillas
    se vuelven a duplicar al envolver el literal remoto (Vantix)."""
    if not comentario:
        return ""
    likes = " OR ".join(
        f"{columna} LIKE '%{str(v).replace(chr(39), chr(39) * 2)}%'" for v in comentario
    )
    return f" AND ({likes})"

def construir_query_muestreo(select_clause: str, from_where_clause: str, cantidad: int,
                             por_operador: bool, por_tipificacion: bool, 
                             col_operador: str, col_tipificacion: str,
                             ctes_clause: str = "") -> str:
    """
    Construye la consulta SQL dinámicamente. 
    Soporta consultas simples y consultas con CTEs previas (pasadas en ctes_clause).
    """
    partition_cols = []
    if por_operador and col_operador:
        partition_cols.append(col_operador)
    if por_tipificacion and col_tipificacion:
        partition_cols.append(col_tipificacion)
        
    if partition_cols:
        partition_str = ", ".join(partition_cols)
        base_cte_name = "CTE_Base_Muestreo"
        
        if ctes_clause:
            # Asume que ctes_clause empieza con "WITH ..." y NO termina en coma.
            query = f"""
            {ctes_clause},
            {base_cte_name} AS (
                SELECT {select_clause}
                {from_where_clause}
            ),
            CTE_Ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition_str} ORDER BY NEWID()) AS _RankingMuestreo
                FROM {base_cte_name}
            )
            SELECT * FROM CTE_Ranked 
            WHERE _RankingMuestreo <= {cantidad}
            """
        else:
            query = f"""
            WITH {base_cte_name} AS (
                SELECT {select_clause}
                {from_where_clause}
            ),
            CTE_Ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition_str} ORDER BY NEWID()) AS _RankingMuestreo
                FROM {base_cte_name}
            )
            SELECT * FROM CTE_Ranked 
            WHERE _RankingMuestreo <= {cantidad}
            """
        return query
    else:
        query = f"{ctes_clause}\n" if ctes_clause else ""
        query += f"""
        SELECT TOP ({cantidad}) {select_clause}
        {from_where_clause}
        ORDER BY NEWID()
        """
        return query


def get_filtered_data_mitrol_puro(engine: Engine, cantidad:int=1, idInteraccion:Union[list[str], None]=None, Segmento:Union[list[int], None]=None, Fecha_desde:Union[datetime, None]=None, Fecha_hasta:Union[datetime, None]=None, loginid:Union[list[str], None]=None, empresa:Union[list[str], None]=None, campana:Union[list[str], None]=None, duracion_min:Union[int, None]=None, duracion_max:Union[int, None]=None, tipificacion:Union[list[str], None]=None, sentido:Union[str, None]=None, comentario:Union[list[str], None]=None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False) -> pd.DataFrame:
    
    select_clause = """
        g.idInteraccion, g.Segmento, [Campaña], d.loginId, d.Tipificación, d.Sentido, d.CRM, d.inicio, [FileName], [FilePath]
    """
    
    from_where_clause = """
    FROM [Acme].[dbo].[detalle de grabaciones] g
    JOIN detalle_de_interacciones_por_campana_lote d on d.idInteraccion = g.idInteraccion and d.Segmento = g.Segmento
    WHERE d.Tipificación != 'Cliente No Responde'
    """
    
    if not reauditar:
        from_where_clause += " AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.[IdAplicativo] = g.idInteraccion + '_' + cast(g.Segmento as varchar(2)))"
    
    if Segmento:
        from_where_clause += f" AND g.Segmento in ({','.join(f'{s}' for s in Segmento)}, 'No buscar')"
    if idInteraccion:
        from_where_clause += f" AND g.idInteraccion in ({','.join(f'{repr(id)}' for id in idInteraccion)}, 'No buscar')"
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if Fecha_desde else None
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if Fecha_hasta else None
        if fd_str and fh_str:
            from_where_clause += f" AND fecha_inicio BETWEEN '{fd_str} 00:00:00' AND '{fh_str} 23:59:59'"
        else:
            f_str = fd_str or fh_str
            from_where_clause += f" AND fecha_inicio >= '{f_str} 00:00:00' AND fecha_inicio <= '{f_str} 23:59:59'"
    if loginid:
        from_where_clause += f" AND loginId in ({','.join(f'{repr(l)}' for l in loginid)}, 'No buscar')"
    if empresa:
        from_where_clause += f" AND empresa in ({','.join(f'{repr(e)}' for e in empresa)}, 'No buscar')"
    if campana:
        from_where_clause += f" AND Campaña in ({','.join(f'{repr(c)}' for c in campana)}, 'No buscar')"
    if duracion_min:
        from_where_clause += f" AND duración >= {duracion_min}"
    if duracion_max:
        from_where_clause += f" AND duración <= {duracion_max}"
    if tipificacion:
        from_where_clause += f" AND Tipificación in ({','.join(f'{repr(t)}' for t in tipificacion)}, 'No buscar')"
    if sentido:
        direccion_base = {'Entrante':'Entrante', 'Saliente':'Saliente', 'Interno':'Interna Saliente'}
        direccion = [direccion_base[d] for d in sentido if d in direccion_base]
        from_where_clause += f" AND Sentido in ({','.join(f'{repr(s)}' for s in direccion)}, 'No buscar')"
    # Filtro de comentario (OR + LIKE) sobre el CRM de la interacción Mitrol.
    from_where_clause += _filtro_comentario_like('d.CRM', comentario)

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "loginId", "Tipificación")
    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def get_filtered_data_Hidra(engine: Engine, cantidad:int=1, idInteraccion:Union[list[str], None]=None, Segmento:Union[list[int], None]=None, Fecha_desde:Union[datetime, None]=None, Fecha_hasta:Union[datetime, None]=None, loginid:Union[list[str], None]=None, duracion_min:Union[int, None]=None, duracion_max:Union[int, None]=None, tipificacion:Union[list[str], None]=None, sentido:Union[list[str], None]=None, comentario:Union[list[str], None]=None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False) -> pd.DataFrame:
    
    ctes_clause = """
    WITH DetalleConSiguienteInicio AS (
        SELECT d.idInteraccion, d.LoginId, d.segmento, d.Tipificación, d.inicio, d.Sentido, d.Campaña, d.Duración, d.CRM,
            LEAD(d.Inicio) OVER (PARTITION BY d.LoginId ORDER BY d.Inicio) AS SiguienteInicio,
            u1.nomina_id, u2.usuario AS Operador
        FROM detalle_de_interacciones_por_campana_lote d
        JOIN usuarios u1 ON d.LoginId = u1.usuario
        JOIN nomina n ON u1.nomina_id = n.id
        JOIN usuarios u2 ON n.id = u2.nomina_id
        WHERE d.Campaña = 'HidraIN' AND d.Tipificación != 'Cliente No Responde' AND u2.usuario IN (SELECT DISTINCT [Legajo] FROM [Acme].[dbo].[Sar_Ingresos])
    """
    
    # Filtro de Fechas (Se asume d.Inicio o d.fecha_inicio si existe en la tabla)
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if Fecha_desde else None
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if Fecha_hasta else None
        if fd_str and fh_str:
            ctes_clause += f" AND d.Inicio BETWEEN '{fd_str} 00:00:00' AND '{fh_str} 23:59:59'"
        else:
            ctes_clause += f" AND d.Inicio >= '{fd_str or fh_str} 00:00:00' AND d.Inicio <= '{fd_str or fh_str} 23:59:59'"
    
    if loginid:
        ctes_clause += f" AND d.loginId in ({','.join(f'{repr(l)}' for l in loginid)})"
        
    ctes_clause += """
    ),
    GrabacionesInteracciones AS (
        SELECT g.idInteraccion, g.Segmento, d.Tipificación, d.Operador, d.loginId, d.Sentido, d.Inicio, d.SiguienteInicio, d.Campaña, d.Duración, d.CRM, g.[FileName], g.[FilePath]
        FROM [Acme].[dbo].[detalle de grabaciones] g
        JOIN DetalleConSiguienteInicio d ON d.idInteraccion = g.idInteraccion AND d.Segmento = g.Segmento
        WHERE 1=1
    """
    
    if not reauditar:
        ctes_clause += " AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = g.idInteraccion + '_' + cast(g.Segmento AS VARCHAR(50)))"
    
    if Segmento:
        ctes_clause += f" AND g.Segmento in ({','.join(f'{s}' for s in Segmento)})"
    if idInteraccion:
        ctes_clause += f" AND d.idInteraccion in ({','.join(f'{repr(id)}' for id in idInteraccion)})"
    if duracion_min:
        ctes_clause += f" AND d.duración >= {duracion_min}"
    if duracion_max:
        ctes_clause += f" AND d.duración <= {duracion_max}"
    if tipificacion:
        ctes_clause += f" AND d.Tipificación in ({','.join(f'{repr(t)}' for t in tipificacion)})"
    if sentido:
        direccion_base = {'Entrante':'Entrante', 'Saliente':'Saliente', 'Interno':'Interna Saliente'}
        direccion = [direccion_base[d] for d in sentido if d in direccion_base]
        ctes_clause += f" AND d.Sentido in ({','.join(f'{repr(s)}' for s in direccion)})"
        
    # El Nro. ODT viene con formato 'R-<año>-<dígitos>'. El enlace con la interacción
    # se hace contra esos dígitos (lo que queda después del 2° guion), expuestos en la
    # columna computada persistida e indexada Sar_Ingresos.ODT_Digitos, que es lo que
    # guarda la columna CRM de detalle_de_interacciones_por_campana_lote.
    #
    # OJO: la numeración del ODT REARRANCA CADA AÑO y el CRM guarda sólo los dígitos
    # (sin el año), así que los dígitos solos son ambiguos: R-2024-298761,
    # R-2025-298761 y R-2026-298761 conviven en Sar_Ingresos (1,26M de 2,57M filas
    # caen en grupos de dígitos repetidos). Sin acotar por fecha, el RowNum=1 de más
    # abajo (ORDER BY [Fecha de Ing.]) se quedaba siempre con el ODT MÁS VIEJO, o sea
    # con el caso de 2024/2025: el audio y la tipificación de Mitrol quedaban bien y
    # todo el bloque de SAR (ODT, Motivo, Observación) correspondía a otro caso.
    # Para desambiguar se acota el ingreso a la ventana del llamado: nunca posterior
    # a Inicio + 30' (margen para el ODT que el operador carga durante/al cierre del
    # llamado; medido sobre datos reales, ninguno se carga más tarde) y nunca más
    # viejo que 180 días (cubre las reiteraciones —el 99,9% de los enlaces reales cae
    # dentro de 90 días— y queda muy por debajo del año en que se repite la numeración).
    ctes_clause += """
    ),
    IngresosRelacionados AS (
        SELECT gi.*, s.[Nro. ODT], s.Motivo, s.Observacion,
            ROW_NUMBER() OVER (PARTITION BY gi.idInteraccion, gi.Segmento ORDER BY s.[Fecha de Ing.]) AS RowNum
        FROM GrabacionesInteracciones gi
        LEFT JOIN [Acme].[dbo].[Sar_Ingresos] s ON
            (
                -- CRM presente: enlace por Nro. ODT (solo ODT que empiezan con 'R',
                -- contra los dígitos tras el 2° guion vía la columna computada ODT_Digitos)
                gi.CRM IS NOT NULL AND LTRIM(RTRIM(gi.CRM)) <> ''
                AND s.[Nro. ODT] LIKE 'R%'
                AND LTRIM(RTRIM(gi.CRM)) = s.ODT_Digitos
                -- Desambiguación por año: sólo el ingreso de la ventana del llamado
                AND s.[Fecha de Ing.] <= DATEADD(MINUTE, 30, gi.Inicio)
                AND s.[Fecha de Ing.] > DATEADD(DAY, -180, gi.Inicio)
            )
            OR
            (
                -- CRM vacío: enlace por operador dentro de la ventana del llamado
                -- (mismo criterio temporal previo, similar a Farmalux)
                (gi.CRM IS NULL OR LTRIM(RTRIM(gi.CRM)) = '')
                AND gi.Operador = s.Legajo
                AND s.[Fecha de Ing.] > DATEADD(SECOND, 30, gi.Inicio)
                AND s.[Fecha de Ing.] < CASE WHEN gi.SiguienteInicio IS NOT NULL THEN DATEADD(SECOND, 15, gi.SiguienteInicio) ELSE DATEADD(MINUTE, 30, gi.Inicio) END
            )
    )
    """

    select_clause = """
        ir.idInteraccion, ir.Segmento, ir.LoginId, ir.Sentido, ir.inicio, ir.[Nro. ODT], ir.Motivo, ir.Observacion, ir.Campaña, ir.Tipificación, sc.Telefono, ir.[FileName], ir.[FilePath]
    """
    from_where_clause = """
    FROM IngresosRelacionados ir
    LEFT JOIN Sar_Consultas sc ON ir.[Nro. ODT] = sc.Numero
    WHERE (ir.RowNum = 1 OR ir.RowNum IS NULL)
      AND (ir.Motivo NOT IN ('Comercial', 'Comunicación Interrumpida', 'Consulta Técnica') OR ir.Motivo IS NULL)
    """
    # Filtro de comentario (OR + LIKE) sobre la Observación del ingreso (Sar).
    from_where_clause += _filtro_comentario_like('ir.Observacion', comentario)

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "LoginId", "Tipificación", ctes_clause)
    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def get_filtered_data_Hidra_comercial(engine: Engine, cantidad:int=1, idInteraccion:Union[list[str], None]=None, Fecha_desde:Union[datetime, None]=None, Fecha_hasta:Union[datetime, None]=None, loginid:Union[list[str], None]=None, duracion_min:Union[int, None]=None, duracion_max:Union[int, None]=None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False) -> pd.DataFrame:
    
    select_clause = "i.[ID], i.[CallLocalTime], i.[Duration] AS [Duracion], 'Entrante' AS [Sentido], i.[EndByAgent], i.[Rec_Filename], i.[Rec_AgentId]"
    from_where_clause = "FROM [Acme].[Hidra].[interacciones] i INNER JOIN (SELECT DISTINCT usuario FROM [Acme].dbo.usuarios) u ON u.usuario = CAST(i.[Rec_AgentId] AS VARCHAR(50)) WHERE 1=1"
    
    if not reauditar:
        from_where_clause += " AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo  = cast(i.[ID] AS VARCHAR(50)))"
        
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if Fecha_desde else None
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if Fecha_hasta else None
        if fd_str and fh_str:
            from_where_clause += f" AND i.[CallLocalTime] BETWEEN '{fd_str} 00:00:00' AND '{fh_str} 23:59:59'"
        else:
            f_str = fd_str or fh_str
            from_where_clause += f" AND i.[CallLocalTime] >= '{f_str} 00:00:00' AND i.[CallLocalTime] <= '{f_str} 23:59:59'"
            
    if loginid:
        from_where_clause += f" AND i.[Rec_AgentId] in ({','.join(f'{repr(l)}' for l in loginid)}, '0')"
    if idInteraccion:
        from_where_clause += f" AND i.[ID] in ({','.join(f'{repr(id)}' for id in idInteraccion)}, 'No buscar')"
    if duracion_min:
        from_where_clause += f" AND i.[Duration] >= {duracion_min}"
    if duracion_max:
        from_where_clause += f" AND i.[Duration] <= {duracion_max}"

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "[Rec_AgentId]", "")
    df = pd.read_sql(query, engine)
    
    if por_operador and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def get_filtered_data_Aurora_salud(engine: Engine, cantidad:int=1, idInteraccion:Union[List[str], None]=None, Segmento:Union[List[int], None]=None, Fecha_desde:Union[datetime, None]=None, Fecha_hasta:Union[datetime, None]=None, loginid:Union[List[str], None]=None, duracion_min:Union[int, None]=None, duracion_max:Union[int, None]=None, tipificacion:Union[List[str], None]=None, sentido:Union[List[str], None]=None, comentario:Union[List[str], None]=None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False) -> pd.DataFrame:
    
    ctes_clause = """
    WITH DetalleConSiguienteInicio AS (
        SELECT d.idInteraccion, d.segmento, d.Tipificación, d.Empresa, d.Campaña, d.inicio, d.Duración,
            LEAD(d.Inicio) OVER (PARTITION BY d.LoginId ORDER BY d.Inicio) AS SiguienteInicio, LoginId AS Operador, d.Sentido
        FROM detalle_de_interacciones_por_campana_lote d
        WHERE Empresa = 'Aurora Salud' AND d.Tipificación NOT IN ('Contestador', 'llamada fallida', 'No corresponde numero', 'No Disp.', 'No responde', 'Numero equivocado', 'Sin comunicacion')
    """
    
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if Fecha_desde else None
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if Fecha_hasta else None
        if fd_str and fh_str:
            ctes_clause += f" AND fecha_inicio BETWEEN '{fd_str} 00:00:00' AND '{fh_str} 23:59:59'"
        else:
            f_str = fd_str or fh_str
            ctes_clause += f" AND fecha_inicio >= '{f_str} 00:00:00' AND fecha_inicio <= '{f_str} 23:59:59'"
    
    if loginid:
        ctes_clause += f" AND loginId in ({','.join(f'{repr(l)}' for l in loginid)}, 'No buscar')"
        
    ctes_clause += """
    ),
    GrabacionesInteracciones AS (
        SELECT g.idInteraccion, g.Segmento, d.Tipificación, d.Empresa, d.Campaña, d.Operador, d.Sentido, d.Inicio, d.SiguienteInicio, d.Duración, g.[FileName], g.[FilePath], g.[Grabacion], g.[Chat], d.inicio as inicio_real
        FROM [Acme].[dbo].[detalle de grabaciones] g
        JOIN DetalleConSiguienteInicio d ON d.idInteraccion = g.idInteraccion AND d.Segmento = g.Segmento
        WHERE 1=1
    """
    
    if not reauditar:
        ctes_clause += " AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = g.idInteraccion + '_' + cast(g.Segmento AS VARCHAR(50)))"
        
    if Segmento:
        ctes_clause += f" AND g.Segmento in ({','.join(f'{s}' for s in Segmento)}, 'No buscar')"
    if idInteraccion:
        ctes_clause += f" AND d.idInteraccion in ({','.join(f'{repr(id)}' for id in idInteraccion)}, 'No buscar')"
    if duracion_min:
        ctes_clause += f" AND d.duración >= {duracion_min}"
    if duracion_max:
        ctes_clause += f" AND d.duración <= {duracion_max}"
    if tipificacion:
        ctes_clause += f" AND d.Tipificación in ({','.join(f'{repr(t)}' for t in tipificacion)}, 'No buscar')"
    if sentido:
        direccion_base = {'Entrante':'Entrante', 'Saliente':'Saliente', 'Interno':'Interna Saliente'}
        direccion = [direccion_base[d] for d in sentido if d in direccion_base]
        ctes_clause += f" AND Sentido in ({','.join(f'{repr(s)}' for s in direccion)}, 'No buscar')"

    ctes_clause += """
    ),
    UsuariosNomina AS (
        SELECT g.*, (n.apellido + ', ' + n.nombre) AS NombreCompleto
        FROM GrabacionesInteracciones g
        JOIN usuarios u ON g.Operador = u.usuario
        JOIN nomina n ON n.id = u.nomina_id
    ),
    TurnosRelacionados AS (
        SELECT un.*, mt.[Paciente ID], mt.[Prestacion ID], mt.[Servicio ID], mt.Observaciones,
            ROW_NUMBER() OVER (PARTITION BY un.idInteraccion, un.Segmento ORDER BY mt.[Fecha Hora Otorgamiento]) AS RowNum
        FROM UsuariosNomina un
        LEFT JOIN [Acme].[dbo].[Aurora Salud Turnos] mt ON un.NombreCompleto = mt.[Usuario Asigna]
            AND mt.[Fecha Hora Otorgamiento] > DATEADD(SECOND, 30, un.Inicio)
            AND mt.[Fecha Hora Otorgamiento] < DATEADD(SECOND, 15, un.SiguienteInicio)
    )
    """

    select_clause = """
        tr.idInteraccion, min(tr.Segmento) Segmento, min(tr.Tipificación) Tipificación, min(tr.Empresa) Empresa, min(tr.Operador) Operador,
        min(tr.Sentido) Sentido, min(tr.Campaña) Campaña,
        CASE WHEN count(mp.[Correo Electronico]) > 0 THEN STRING_AGG(mp.[Correo Electronico], CHAR(13) + CHAR(10)) ELSE NULL END AS [Correo Electronico],
        CASE WHEN count(mp.Telefonos) > 0 THEN STRING_AGG(mp.Telefonos, CHAR(13) + CHAR(10)) ELSE NULL END AS Telefonos,
        CASE WHEN count(mnp.Prestacion) > 0 THEN STRING_AGG(mnp.Prestacion, CHAR(13) + CHAR(10)) ELSE NULL END AS Prestacion,
        CASE WHEN count(mns.Servicio) > 0 THEN STRING_AGG(mns.Servicio, CHAR(13) + CHAR(10)) ELSE NULL END AS Servicio,
        CASE WHEN count(tr.Observaciones) > 0 THEN STRING_AGG(tr.Observaciones, CHAR(13) + CHAR(10)) ELSE NULL END AS Observaciones,
        CASE WHEN count(mnp.Prestacion) > 0 THEN count(*) ELSE NULL END AS Cantidad_turnos,
        min(tr.[FileName]) FileName, min(tr.[FilePath]) FilePath, max(CAST(tr.[Grabacion] AS INT)) AS Grabacion, max(CAST(tr.[Chat] AS INT)) AS Chat, tr.inicio_real as inicio
    """
    
    from_where_clause = """
    FROM TurnosRelacionados tr
    LEFT JOIN [Aurora Salud Pacientes] mp ON mp.[Paciente ID] = tr.[Paciente ID]
    LEFT JOIN [Aurora Salud Normalizador Prestaciones] mnp ON mnp.[Prestacion ID] = tr.[Prestacion ID]
    LEFT JOIN [Aurora Salud Normalizador Servicios] mns ON mns.[Servicio ID] = tr.[Servicio ID]
    GROUP BY tr.idInteraccion, tr.inicio_real
    """
    # Filtro de comentario (OR + LIKE) sobre Observaciones (que vienen agregadas con
    # STRING_AGG): se incluye el llamado si ALGUNA de sus observaciones matchea, vía
    # HAVING sobre el grupo (no rompe la agregación de la columna final).
    if comentario:
        _likes = " OR ".join(f"tr.Observaciones LIKE '%{str(v).replace(chr(39), chr(39) * 2)}%'" for v in comentario)
        from_where_clause += f"\n    HAVING MAX(CASE WHEN ({_likes}) THEN 1 ELSE 0 END) = 1"

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "Operador", "Tipificación", ctes_clause)
    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def get_filtered_data_ALARMIX(engine: Engine, cantidad: int = 1, idInteraccion: Union[List[str], None] = None, Fecha_desde: Union[datetime, None] = None, Fecha_hasta: Union[datetime, None] = None, direccion: Union[List[str], None] = None, Empleado: Union[List[str], None] = None, tipificacion: Union[List[str], None] = None, skill: Union[List[str], None] = None, duracion_min: Union[int, None] = None, duracion_max: Union[int, None] = None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar: bool = False) -> pd.DataFrame:
    
    filtros_grabaciones = "1=1"
    filtros_interacciones = ""  
    
    if not reauditar:
        filtros_grabaciones += " AND NOT EXISTS (SELECT 1 FROM [Acme].[calidad].[Auditorias] a WHERE a.[IdAplicativo] = CAST(d.[segmentId] AS VARCHAR(MAX)))"

    if idInteraccion:
        filtros_grabaciones += f" AND d.[segmentId] in ({','.join(f'{repr(str(id))}' for id in idInteraccion)}, 'No buscar')"
    if direccion:
        direccion_base = {'Entrante': 'IN_BOUND', 'Saliente': 'OUT_BOUND', 'Interno': 'INTERNAL'}
        dir_mapped = [direccion_base[dir] for dir in direccion if dir in direccion_base]
        filtros_grabaciones += f" AND d.[directionType] in ({','.join(f'{repr(d)}' for d in dir_mapped)}, 'No buscar')"
    if duracion_min:
        filtros_grabaciones += f" AND d.[duration] >= {duracion_min}"
    if duracion_max:
        filtros_grabaciones += f" AND d.[duration] <= {duracion_max}"
    if Empleado:
        filtros_grabaciones += f" AND d.[agentIds] in ({','.join(f'{repr(e)}' for e in Empleado)}, '0')"
    if tipificacion:
        filtros_grabaciones += f" AND d.[callTagging] in ({','.join(f'{repr(t)}' for t in tipificacion)}, 'No buscar')"
    if skill:
        filtros_grabaciones += f" AND d.[skillName] in ({','.join(f'{repr(s)}' for s in skill)}, 'No buscar')"
    
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if isinstance(Fecha_desde, datetime) else Fecha_desde
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if isinstance(Fecha_hasta, datetime) else Fecha_hasta
        if Fecha_desde and Fecha_hasta:
            filtros_grabaciones += f" AND cast(d.[segmentContactStartTime] as date) Between '{fd_str}' and '{fh_str}'"
            filtros_interacciones += f" AND [Contact Start Date Time] >= DATEADD(DAY, -1, '{fd_str}') AND [Contact Start Date Time] <= DATEADD(DAY, 1, '{fh_str}')"
        else:
            f_str = fd_str if fd_str else fh_str
            filtros_grabaciones += f" AND cast(d.[segmentContactStartTime] as date) = '{f_str}'"
            filtros_interacciones += f" AND cast([Contact Start Date Time] as date) Between DATEADD(DAY, -1, '{f_str}') AND DATEADD(DAY, 1, '{f_str}')"
    else:
        filtros_interacciones += " AND [Contact Start Date Time] >= DATEADD(DAY, -30, GETDATE())"

    ctes_clause = f"""
    WITH InteraccionesFiltradas AS (
        SELECT [Agent ID], [Contact Start Date Time], [Motivo del fin del contacto]
        FROM [Acme].[dbo].[ALARMIX Detalle interacciones] WHERE 1=1 {filtros_interacciones}
    ),
    GrabacionesAjustadas AS (
        SELECT d.[segmentId], d.agentName_sort, d.agentIds, d.[directionType], d.[callTagging], DATEADD(HOUR, -3, d.[segmentContactStartTime]) AS Inicio_Grabacion_Local
        FROM [Acme].[ALARMIX].[Grabaciones] d WHERE {filtros_grabaciones}
    ),
    CruceBruto AS (
        SELECT g.[segmentId], g.agentName_sort, g.Inicio_Grabacion_Local AS inicio, g.[directionType], g.[callTagging], i.[Motivo del fin del contacto],
            ROW_NUMBER() OVER(PARTITION BY g.[segmentId] ORDER BY i.[Contact Start Date Time] DESC) AS FilaNumero
        FROM GrabacionesAjustadas g
        LEFT JOIN InteraccionesFiltradas i ON i.[Agent ID] = g.agentIds AND i.[Contact Start Date Time] <= g.Inicio_Grabacion_Local AND i.[Contact Start Date Time] >= DATEADD(HOUR, -2, g.Inicio_Grabacion_Local)
    )
    """

    select_clause = "[segmentId], agentName_sort as Operador, inicio, [directionType], [callTagging] as Tipificación, [Motivo del fin del contacto]"
    from_where_clause = "FROM CruceBruto WHERE FilaNumero = 1"
    
    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "Operador", "Tipificación", ctes_clause)
    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def get_filtered_data_Vitalis_Salud(engine: Engine, cantidad: int = 1, idInteraccion: Union[List[str], None] = None, Fecha_desde: Union[datetime, None] = None, Fecha_hasta: Union[datetime, None] = None, direccion: Union[List[str], None] = None, loginid: Union[List[str], None] = None, tipificacion: Union[List[str], None] = None, duracion_min: Union[int, None] = None, duracion_max: Union[int, None] = None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar: bool = False) -> pd.DataFrame:
    
    select_clause = "[es_entrante], agente_usuario, [subtipo_interaccion] as Tipificación, caso_id, fecha_inicio, [estado_llamada], [url_grabacion]"
    from_where_clause = "FROM [Acme].[dbo].[Vitalis_Llamadas] WHERE agente_usuario like '% Acme' and url_grabacion is not null "
    
    if not reauditar:
        from_where_clause += " AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = cast([caso_id] as varchar))"
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if Fecha_desde else None
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if Fecha_hasta else None
        if fd_str and fh_str:
            from_where_clause += f" AND cast([fecha_inicio] as date) BETWEEN '{fd_str}' AND '{fh_str}'"
        else:
            from_where_clause += f" AND cast([fecha_inicio] as date) = '{fd_str or fh_str}'"
    if direccion:
        if 'Interno' in direccion: direccion = ['Entrante', 'Saliente']
        direccion_base = {'Entrante': '1', 'Saliente': '0'}
        dir_mapped = [direccion_base[d] for d in direccion if d in direccion_base]
        from_where_clause += f" AND [es_entrante] in ({','.join(dir_mapped)}, -1)"
    if tipificacion:
        from_where_clause += f" AND [subtipo_interaccion] in ({','.join(f'{repr(t)}' for t in tipificacion)}, 'No buscar')"
    if loginid:
        from_where_clause += f" AND [agente_usuario] in ({','.join(f'{repr(l)}' for l in loginid)}, '0')"
    if idInteraccion:
        from_where_clause += f" AND [caso_id] in ({','.join(f'{repr(id)}' for id in idInteraccion)}, 'No buscar')"
    if duracion_min:
        from_where_clause += f" AND [duracion_total] >= {duracion_min}"
    if duracion_max:
        from_where_clause += f" AND [duracion_total] <= {duracion_max}"

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "agente_usuario", "Tipificación")
    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


# Los audios de Voltara ya no se bajan de Verint: el usuario los sube junto con un
# Excel (nombre de archivo -> ConnID). La info del llamado sale de [Voltara informe IVR]
# (ver AuditorIA/Voltara.py).
#
# El cruce con [Voltara_Salesforce_casos_calidad] (caso de gestión "de calidad") sigue
# DESACTIVADO (2026-07-20): esa tabla la modifica un equipo de back-office/QA (~75
# personas, algunas explícitamente "(VOLTARA)" = personal del cliente), una población
# casi disjunta de los agentes que atienden el IVR. Se probó también
# [Voltara_Salesforce_usuarios] (normalizador nombre<->código creado para intentar
# resolver esto) y no ayuda: es el mismo padrón de agentes telefónicos que ya cubría
# [users SF], y sigue sin solaparse con esas ~75 personas.
#
# En cambio [Voltara_Salesforce_casos_cerrados] SÍ tiene vínculo real con el agente
# (columna `Usuario` = mismo código que `Agente` del IVR, ej. "AR10000004"), pero la
# apertura/cierre del caso ocurre DÍAS después del llamado (no en una ventana de
# minutos/horas: se probó y no matchea nada) - el agente trabaja esos casos como un
# backlog asincrónico, no en vivo durante la llamada. Por eso el cruce que sigue NO
# intenta identificar "el caso de este llamado": trae, a modo de contexto, los casos
# que ese mismo agente gestionó en una ventana de +-VOLTARA_MARGEN_DIAS alrededor del
# llamado (agregados: cantidad + lista de casos + motivos), sin asociar ninguno en
# particular a la interacción auditada.
VOLTARA_OFFSET_HORAS_IVR_A_SF = -3
VOLTARA_MARGEN_DIAS = 3


def get_filtered_data_Voltara(engine: Engine, conn_ids: Union[list[str], None] = None) -> pd.DataFrame:
    """Trae la info del llamado ([Voltara informe IVR]) para cada ConnID de los audios
    subidos, más un resumen (a modo de contexto, no de vínculo exacto) de los casos que
    el mismo agente gestionó en Salesforce ([Voltara_Salesforce_casos_cerrados]) en una
    ventana de +-VOLTARA_MARGEN_DIAS alrededor del llamado. Ver la nota arriba.

    Devuelve una fila por ConnID encontrado en el informe IVR (el más reciente si hay
    duplicados). Los campos viajan como columnas del DataFrame y llegan solos al
    prompt de Gemini (ver gemini.py::prompt_details).
    """
    if not conn_ids:
        return pd.DataFrame()

    # Deduplicar preservando orden y saneando. Las comillas simples se duplican para
    # armar el literal SQL (mismo criterio que el resto de los builders).
    ids = [str(c).strip() for c in dict.fromkeys(conn_ids) if c is not None and str(c).strip()]
    if not ids:
        return pd.DataFrame()
    in_list = ", ".join("N'" + c.replace("'", "''") + "'" for c in ids)

    off = VOLTARA_OFFSET_HORAS_IVR_A_SF
    margen = VOLTARA_MARGEN_DIAS
    query = f"""
    WITH voltara_base AS (
        SELECT
            i.[ConnID], i.[ANI], i.[Fecha de Inicio] AS inicio, 'Entrante' AS Sentido,
            i.[Nombre de Agente], i.[Agente], i.[Cola], i.[Skill], i.[BPO],
            i.[Numero de caso], i.[Documento], i.[Tipo de Documento], i.[Suministro],
            i.[Duración Talk] AS Duracion,
            REPLACE(i.[Agente], '_', '') AS agente_cod,
            DATEADD(HOUR, {off}, i.[Fecha de Inicio]) AS inicio_local,
            ROW_NUMBER() OVER (PARTITION BY i.[ConnID] ORDER BY i.[Fecha de Inicio] DESC) AS _rn
        FROM [Acme].[dbo].[Voltara informe IVR] i
        WHERE i.[ConnID] IN ({in_list})
    )
    SELECT
        b.[ConnID], b.[ANI], b.inicio, b.Sentido,
        b.[Nombre de Agente], b.[Agente], b.[Cola], b.[Skill], b.[BPO],
        b.[Numero de caso], b.[Documento], b.[Tipo de Documento], b.[Suministro], b.Duracion,
        c.[Cantidad de casos del agente en el periodo],
        c.[Casos del agente en el periodo],
        m.[Motivos de casos del agente en el periodo]
    FROM voltara_base b
    OUTER APPLY (
        SELECT
            COUNT(*) AS [Cantidad de casos del agente en el periodo],
            STRING_AGG(CAST(x.Caso AS varchar(20)), ', ') WITHIN GROUP (ORDER BY x.apertura)
                AS [Casos del agente en el periodo]
        FROM (
            SELECT DISTINCT sf.[Caso], sf.[Fecha/Hora apertura] AS apertura
            FROM [Acme].[dbo].[Voltara_Salesforce_casos_cerrados] sf
            WHERE REPLACE(sf.[Usuario], '_', '') = b.agente_cod
              AND sf.[Fecha/Hora apertura] BETWEEN DATEADD(DAY, -{margen}, b.inicio_local) AND DATEADD(DAY, {margen}, b.inicio_local)
        ) x
    ) c
    OUTER APPLY (
        SELECT STRING_AGG(y.Motivo, ', ') AS [Motivos de casos del agente en el periodo]
        FROM (
            SELECT DISTINCT sf.[Motivo]
            FROM [Acme].[dbo].[Voltara_Salesforce_casos_cerrados] sf
            WHERE REPLACE(sf.[Usuario], '_', '') = b.agente_cod
              AND sf.[Fecha/Hora apertura] BETWEEN DATEADD(DAY, -{margen}, b.inicio_local) AND DATEADD(DAY, {margen}, b.inicio_local)
              AND sf.[Motivo] IS NOT NULL
        ) y
    ) m
    WHERE b._rn = 1
    """
    return pd.read_sql(query, engine)


def get_filtered_data_Odonto_plus(engine: Engine, cantidad: int = 1, idInteraccion: Union[List[str], None] = None,campana:Union[list[str], None] = None, Segmento: Union[List[int], None] = None, Fecha_desde: Union[datetime, str, None] = None, Fecha_hasta: Union[datetime, str, None] = None, loginid: Union[List[str], None] = None, duracion_min: Union[int, None] = None, duracion_max: Union[int, None] = None, tipificacion: Union[List[str], None] = None, sentido: Union[List[str], None] = None, comentario: Union[List[str], None] = None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar: bool = False) -> pd.DataFrame:
    """Consulta optimizada de Odonto Plus adaptada a la nueva parametrización"""
    query = "SET NOCOUNT ON;\n"

    # PASO 1
    query += """
    SELECT d.idInteraccion, d.segmento, d.Tipificación, d.Empresa, d.Campaña, d.inicio, d.Duración, d.LoginId AS Operador, d.Sentido, d.Cliente,
        LEAD(d.inicio) OVER (PARTITION BY d.LoginId ORDER BY d.inicio) AS SiguienteInicio
    INTO #InteraccionesBase
    FROM detalle_de_interacciones_por_campana_lote d
    WHERE d.Empresa in ('Odonto Plus','Facebook') 
      AND d.Tipificación NOT IN ('No responde', 'No Disp.', 'No esta Interesado', 'Numero Equivocado', 'Número equivocado', 'Volver a llamar')
    """
    
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if isinstance(Fecha_desde, datetime) else Fecha_desde
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if isinstance(Fecha_hasta, datetime) else Fecha_hasta
        if fd_str and fh_str:
            query += f"  AND d.fecha_inicio BETWEEN '{fd_str} 00:00:00' AND '{fh_str} 23:59:59'\n"
        else:
            f_str = fd_str or fh_str
            query += f"  AND d.fecha_inicio >= '{f_str} 00:00:00' AND d.fecha_inicio <= '{f_str} 23:59:59'\n"
    if loginid:
        query += f"  AND d.LoginId IN ({','.join(f'{repr(l)}' for l in loginid)}, 'No buscar')\n"
    if Segmento:
        query += f"  AND d.segmento IN ({','.join(f'{s}' for s in Segmento)}, -1)\n"
    if idInteraccion:
        query += f"  AND d.idInteraccion IN ({','.join(f'{repr(id)}' for id in idInteraccion)}, 'No buscar')\n"
    if tipificacion: query += f"  AND d.Tipificación IN ({','.join(f'{repr(t)}' for t in tipificacion)}, 'No buscar')\n"
    if sentido:
        direccion_base = {'Entrante': 'Entrante', 'Saliente': 'Saliente', 'Interno': 'Interna Saliente'}
        dir_validas = [direccion_base[s] for s in sentido if s in direccion_base]
        if dir_validas: query += f"  AND d.Sentido IN ({','.join(f'{repr(s)}' for s in dir_validas)}, 'No buscar')\n"
    if campana: query += f"  AND d.Campaña IN ({','.join(f'{repr(c)}' for c in campana)}, 'No buscar')\n"


    # PASO 2: Cruzar con Grabaciones y extraer ranking si es necesario
    partition_cols = []
    if por_operador: partition_cols.append("Operador")
    if por_tipificacion: partition_cols.append("Tipificación")
    
    auditoria_filter = ""
    if not reauditar:
        auditoria_filter = " AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.[IdAplicativo] = g.idInteraccion + '_' + cast(g.Segmento as varchar(2)))"

    # La duración solo filtra llamados, nunca chats: un chat queda abierto de punta a punta
    # de la gestión (promedio ~66 min), así que el rango del slider (60-600s por defecto)
    # descartaba el 92% de los chats. Se aplica acá y no en el PASO 1 porque el flag Chat
    # vive en [detalle de grabaciones] (g), no en la tabla de interacciones (b).
    duracion_cond = []
    if duracion_min: duracion_cond.append(f"b.Duración >= {duracion_min}")
    if duracion_max: duracion_cond.append(f"b.Duración <= {duracion_max}")
    duracion_filter = f" AND (ISNULL(g.Chat, 0) = 1 OR ({' AND '.join(duracion_cond)}))" if duracion_cond else ""

    # Filtro de comentario (OR + LIKE) sobre las Observaciones del Turnero. El comentario
    # se enlaza recién en PASO 3 (OUTER APPLY, post-muestreo), así que para filtrar ANTES
    # del muestreo replicamos ese enlace como EXISTS sobre #InteraccionesBase (alias b).
    comentario_filter = ""
    if comentario:
        _likes = " OR ".join(f"dt.Observaciones LIKE '%{str(v).replace(chr(39), chr(39) * 2)}%'" for v in comentario)
        comentario_filter = f"""
            AND EXISTS (
                SELECT 1 FROM [Acme].[dbo].[Odonto_Plus_Turnero] dt
                INNER JOIN usuarios u_crm ON dt.[operador] = u_crm.usuario
                INNER JOIN usuarios u_tel ON b.Operador = u_tel.usuario
                WHERE u_crm.nomina_id = u_tel.nomina_id
                  AND (
                      (
                          dt.FechaAlta >= DATEADD(MINUTE, -5, b.inicio)
                          AND dt.FechaAlta <= DATEADD(HOUR, 3, b.inicio)
                          AND b.Cliente IS NOT NULL
                          AND (
                              (dt.celular IS NOT NULL AND LEN(dt.celular) >= 8 AND b.Cliente LIKE '%' + RIGHT(dt.celular, 8) + '%')
                              OR (dt.telefono IS NOT NULL AND LEN(dt.telefono) >= 8 AND ISNUMERIC(RIGHT(dt.telefono, 8)) = 1 AND b.Cliente LIKE '%' + RIGHT(dt.telefono, 8) + '%')
                          )
                      )
                      OR (
                          ISNULL(g.Chat, 0) = 0
                          AND dt.FechaAlta > DATEADD(SECOND, 30, b.inicio)
                          AND (b.SiguienteInicio IS NULL OR dt.FechaAlta < DATEADD(SECOND, 15, b.SiguienteInicio))
                      )
                  )
                  AND ({_likes})
            )"""

    if partition_cols:
        part_str = ", ".join(partition_cols)
        query += f"""
        ;
        WITH SeleccionBruta AS (
            SELECT g.idInteraccion, g.Segmento, b.Tipificación, b.Operador, b.Sentido, b.Empresa, b.Campaña, b.inicio, b.SiguienteInicio, b.Cliente, g.[FileName], g.[FilePath], g.[Grabacion], g.[Chat]
            FROM [Acme].[dbo].[detalle de grabaciones] g
            INNER JOIN #InteraccionesBase b ON b.idInteraccion = g.idInteraccion AND b.Segmento = g.Segmento
            WHERE 1=1 {auditoria_filter}{comentario_filter}{duracion_filter}
        ),
        Ranked AS (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY {part_str} ORDER BY NEWID()) as _rnk FROM SeleccionBruta
        )
        SELECT idInteraccion, Segmento, Tipificación, Operador, Sentido, Empresa, Campaña, inicio, SiguienteInicio, Cliente, [FileName], [FilePath], [Grabacion], [Chat]
        INTO #SeleccionAleatoria FROM Ranked WHERE _rnk <= {cantidad};
        """
    else:
        query += f"""
        ;
        SELECT TOP ({cantidad}) g.idInteraccion, g.Segmento, b.Tipificación, b.Operador, b.Sentido, b.Empresa, b.Campaña, b.inicio, b.SiguienteInicio, b.Cliente, g.[FileName], g.[FilePath], g.[Grabacion], g.[Chat]
        INTO #SeleccionAleatoria FROM [Acme].[dbo].[detalle de grabaciones] g
        INNER JOIN #InteraccionesBase b ON b.idInteraccion = g.idInteraccion AND b.Segmento = g.Segmento
        WHERE 1=1 {auditoria_filter}{comentario_filter}{duracion_filter} ORDER BY NEWID();
        """

    # PASO 3 y 4
    query += """
    SELECT s.idInteraccion, s.Segmento, s.Tipificación, s.Operador, s.Sentido, s.Empresa, s.Campaña, Turno.email, Turno.celular, Turno.FechaHora, Turno.[esprimeravez], Turno.Observaciones, Turno.IdClinica, Turno.IdTurno, s.inicio, s.[FileName], s.[FilePath], s.[Grabacion], s.[Chat]
    FROM #SeleccionAleatoria s
    OUTER APPLY (
        SELECT TOP 1 dt.email, dt.celular, dt.FechaHora, dt.[esprimeravez], dt.Observaciones, dt.IdClinica, dt.IdTurno
        FROM [Acme].[dbo].[Odonto_Plus_Turnero] dt
        INNER JOIN usuarios u_crm ON dt.[operador] = u_crm.usuario 
        INNER JOIN usuarios u_tel ON s.Operador = u_tel.usuario
        WHERE u_crm.nomina_id = u_tel.nomina_id
          AND (
              -- Coincidencia por teléfono (Aplica con máxima prioridad a CHATS y LLAMADAS):
              -- En chats los operadores atienden en paralelo, por lo que el teléfono es imprescindible
              -- para no cruzar turnos de distintos pacientes atendidos al mismo tiempo.
              (
                  dt.FechaAlta >= DATEADD(MINUTE, -5, s.inicio)
                  AND dt.FechaAlta <= DATEADD(HOUR, 3, s.inicio)
                  AND s.Cliente IS NOT NULL
                  AND (
                      (dt.celular IS NOT NULL AND LEN(dt.celular) >= 8 AND s.Cliente LIKE '%' + RIGHT(dt.celular, 8) + '%')
                      OR (dt.telefono IS NOT NULL AND LEN(dt.telefono) >= 8 AND ISNUMERIC(RIGHT(dt.telefono, 8)) = 1 AND s.Cliente LIKE '%' + RIGHT(dt.telefono, 8) + '%')
                  )
              )
              -- Fallback por ventana de tiempo (SOLO PARA LLAMADAS TELEFÓNICAS, nunca para chats):
              -- En llamadas el operador atiende de a una persona por vez; si el paciente llamó
              -- desde otro teléfono se conserva el cruce por ventana entre llamadas.
              OR (
                  ISNULL(s.Chat, 0) = 0
                  AND dt.FechaAlta > DATEADD(SECOND, 30, s.inicio) 
                  AND (s.SiguienteInicio IS NULL OR dt.FechaAlta < DATEADD(SECOND, 15, s.SiguienteInicio))
              )
          )
        ORDER BY 
          CASE 
              WHEN s.Cliente IS NOT NULL AND (
                  (dt.celular IS NOT NULL AND LEN(dt.celular) >= 8 AND s.Cliente LIKE '%' + RIGHT(dt.celular, 8) + '%')
                  OR (dt.telefono IS NOT NULL AND LEN(dt.telefono) >= 8 AND ISNUMERIC(RIGHT(dt.telefono, 8)) = 1 AND s.Cliente LIKE '%' + RIGHT(dt.telefono, 8) + '%')
              ) THEN 0 
              ELSE 1 
          END ASC,
          ABS(DATEDIFF(SECOND, s.inicio, dt.FechaAlta)) ASC
    ) AS Turno;
    DROP TABLE #InteraccionesBase;
    DROP TABLE #SeleccionAleatoria;
    """

    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))

    # diccionario_cronograma = diccionario_dental_clinicas()
    
    # def extraer_texto_df(id_clinica, indice):
    #     if pd.isna(id_clinica) or id_clinica not in diccionario_cronograma: return None
    #     df_extraido = diccionario_cronograma[id_clinica][indice]
    #     if isinstance(df_extraido, pd.DataFrame): return df_extraido.to_string(index=False)
    #     return str(df_extraido)

    # df['IdClinica'] = df['IdClinica'].apply(lambda x: int(x) if not pd.isna(x) else x)
    # df['Info_General_Clinica'] = df['IdClinica'].apply(lambda x: extraer_texto_df(x, 0))
    # df['Cronograma_Clinica'] = df['IdClinica'].apply(lambda x: extraer_texto_df(x, 1))
    
    return df

def get_filtered_data_Farmalux(engine: Engine, cantidad:int=1, idInteraccion:Union[List[str], None]=None, Fecha_desde:Union[datetime, None]=None, Fecha_hasta:Union[datetime, None]=None, loginid:Union[List[str], None]=None, duracion_min:Union[int, None]=None, duracion_max:Union[int, None]=None, campana:Union[List[str], None]=None, comentario:Union[List[str], None]=None, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False) -> pd.DataFrame:

    # ID sintético: la tabla no tiene un identificador único propio. Se usa para
    # deduplicar contra calidad.Auditorias y como id_aplicativo en la pipeline.
    id_expr = "CONCAT(b.[Origen], '_', b.[Destino], '_', FORMAT(b.[Fecha/Hora], 'yyyyMMddHHmmss'))"

    # Filtros aplicados dentro del CTE (sobre Grabaciones) para reducir el dataset
    # antes del OUTER APPLY contra SalesForce_casos.
    filtros_cte = ""
    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if isinstance(Fecha_desde, datetime) else Fecha_desde
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if isinstance(Fecha_hasta, datetime) else Fecha_hasta
        if fd_str and fh_str:
            filtros_cte += f" AND CAST(gr.[Fecha/Hora] AS DATE) BETWEEN '{fd_str}' AND '{fh_str}'"
        else:
            filtros_cte += f" AND CAST(gr.[Fecha/Hora] AS DATE) = '{fd_str or fh_str}'"
    if loginid:
        filtros_cte += f" AND gr.[Agente que Atendió] in ({','.join(f'{repr(l)}' for l in loginid)}, 'No buscar')"
    if campana:
        filtros_cte += f" AND gr.[Cola Entrante] in ({','.join(f'{repr(c)}' for c in campana)}, 'No buscar')"
    if duracion_min:
        filtros_cte += f" AND gr.[Duración] >= {duracion_min}"
    if duracion_max:
        filtros_cte += f" AND gr.[Duración] <= {duracion_max}"

    ctes_clause = f"""
    WITH farma_base AS (
        SELECT
            gr.[Fecha/Hora], gr.[Origen], gr.[Destino], gr.[Duración],
            gr.[Estado], gr.[Entidad], gr.[Usuario], gr.[Cola Entrante],
            gr.[Agente que Atendió], gr.[DataFijos], gr.[DataPers],
            n.[UsuarioSalesForce] AS UsuarioSalesForceNorm
        FROM [Acme].[Farmalux].[Grabaciones] gr
        JOIN dbo.usuarios u ON u.usuario = gr.[Agente que Atendió]
        JOIN dbo.nomina nm ON u.nomina_id = nm.id
        JOIN dbo.operadores o ON nm.id = o.legajo_id AND o.estado = 1 AND o.fecha_hasta IS NULL
        JOIN [Acme].[Farmalux].[Normalizador_SalesForce_AsterVoip] n
            ON n.[UsuarioAsterVoip] = gr.[Agente que Atendió]
        WHERE gr.[Cola Entrante] IS NOT NULL
        {filtros_cte}
    )
    """

    select_clause = f"""
        {id_expr} AS id,
        b.[Fecha/Hora] AS fecha_inicio,
        b.[Origen],
        b.[Destino],
        b.[Duración] AS Duracion,
        b.[Estado],
        b.[Entidad],
        b.[Usuario],
        b.[Cola Entrante] AS Campaña,
        b.[Agente que Atendió] AS LoginId,
        b.[DataFijos],
        b.[DataPers],
        s.[Abierto] AS [Caso Abierto],
        s.[Cerrado] AS [Caso Cerrado],
        s.[Comentarios del caso] AS [Comentarios del caso],
        s.[Asunto] AS [Asunto],
        s.[Nombre de la cuenta] AS [Nombre de la cuenta],
        s.[Contacto: Teléfono] AS [Telefono Contacto],
        s.[Número del caso] AS [Numero del caso],
        s.[Voz del cliente] AS [Voz del cliente],
        s.[Motivo] AS [Motivo],
        s.[Submotivo] AS [Submotivo]
    """
    # El caso de SalesForce solo se asocia si su modificación cae dentro del llamado
    # o hasta 30 segundos después de finalizado. Si hay más de uno, se queda con el primero.
    from_where_clause = """
    FROM farma_base b
    OUTER APPLY (
        SELECT TOP 1 sf.[Abierto], sf.[Cerrado], sf.[Comentarios del caso],
                     sf.[Asunto], sf.[Nombre de la cuenta], sf.[Contacto: Teléfono],
                     sf.[Número del caso], sf.[Voz del cliente], sf.[Motivo], sf.[Submotivo]
        FROM [Acme].[Farmalux].[SalesForce_casos] sf
        WHERE sf.[Propietario del caso] = b.UsuarioSalesForceNorm
          AND sf.[Última fecha/hora de modificación de caso] >= b.[Fecha/Hora]
          AND sf.[Última fecha/hora de modificación de caso]
                <= DATEADD(second, ISNULL(b.[Duración], 0) + 30, b.[Fecha/Hora])
        ORDER BY sf.[Última fecha/hora de modificación de caso] ASC
    ) s
    WHERE 1=1
    """

    if not reauditar:
        from_where_clause += f" AND NOT EXISTS (SELECT 1 FROM [Acme].calidad.Auditorias a WHERE a.IdAplicativo = {id_expr})"
    if idInteraccion:
        from_where_clause += f" AND {id_expr} in ({','.join(f'{repr(id)}' for id in idInteraccion)}, 'No buscar')"
    # Filtro de comentario (OR + LIKE) sobre el comentario del caso de SalesForce.
    # El OUTER APPLY 's' ya está en el WHERE (pre-muestreo), así que filtra correctamente.
    from_where_clause += _filtro_comentario_like('s.[Comentarios del caso]', comentario)

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "LoginId", "", ctes_clause)
    df = pd.read_sql(query, engine)

    if por_operador and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def _lista_sql(valores: List[str]) -> str:
    """Lista para un IN (...) con las comillas simples escapadas. `repr()` no sirve: a un
    valor con apóstrofo (un apellido D'Alessandro) le pone comillas dobles y rompe el SQL."""
    return ", ".join("N'" + str(v).replace("'", "''") + "'" for v in valores)


# Operadores de Acme en el Genesys de Benefix: la org es del cliente y también atiende su
# propia gente (los WhatsApp, y parte de la voz). Los de Acme tienen el nombre con el
# prefijo "ACME - " y la casilla @consultores.benefix.example (medido 2026-09-21: 13 de 13
# activos cumplen las dos cosas; se aceptan cualquiera de las dos por si una se olvida).
BENEFIX_OPERADOR_ACME = "(i.[Operador] LIKE N'ACME - %' OR i.[Email] LIKE N'%@consultores.benefix.example')"


def get_filtered_data_Benefix(engine: Engine, cantidad: int = 1, idInteraccion: Union[List[str], None] = None, Fecha_desde: Union[datetime, None] = None, Fecha_hasta: Union[datetime, None] = None, loginid: Union[List[str], None] = None, duracion_min: Union[int, None] = None, duracion_max: Union[int, None] = None, campana: Union[List[str], None] = None, tipificacion: Union[List[str], None] = None, sentido: Union[List[str], None] = None, comentario: Union[List[str], None] = None, por_operador: bool = False, por_tipificacion: bool = False, omitir_limite: bool = False, reauditar: bool = False) -> pd.DataFrame:
    """Llamados de voz grabados de Benefix (Genesys Cloud), de Benefix.Interacciones.

    La tabla la carga scripts/benefix_genesys.py: una fila por tramo de agente. La unidad
    de auditoría es la conversación (IdAplicativo = conversationId, que es también con lo
    que se baja el audio): en las pocas con dos operadores de Acme (4 de 834 en la semana
    medida) se audita el tramo con más tiempo hablado, porque la grabación es una sola.
    `campana` son las colas de Genesys asignadas como skills de la campaña.
    """
    # SegundosHablados > 0: un llamado que sonó y no se atendió figura "grabado" con
    # medio segundo de audio y no hay nada que auditar.
    filtros = f"i.[Canal] = 'voice' AND i.[Grabada] = 1 AND i.[SegundosHablados] > 0 AND {BENEFIX_OPERADOR_ACME}"

    if Fecha_desde or Fecha_hasta:
        fd_str = Fecha_desde.strftime('%Y-%m-%d') if isinstance(Fecha_desde, datetime) else Fecha_desde
        fh_str = Fecha_hasta.strftime('%Y-%m-%d') if isinstance(Fecha_hasta, datetime) else Fecha_hasta
        if fd_str and fh_str:
            filtros += f" AND i.[Fecha] BETWEEN '{fd_str}' AND '{fh_str}'"
        else:
            filtros += f" AND i.[Fecha] = '{fd_str or fh_str}'"
    if idInteraccion:
        filtros += f" AND i.[ConversationId] IN ({_lista_sql(idInteraccion)})"
    if loginid:
        filtros += f" AND i.[Operador] IN ({_lista_sql(loginid)})"
    if campana:
        filtros += f" AND i.[Cola] IN ({_lista_sql(campana)})"
    if tipificacion:
        filtros += f" AND i.[Tipificacion] IN ({_lista_sql(tipificacion)})"
    if sentido:
        # En Genesys no hay "Interno": tildar solo esa opción tiene que traer nada, no
        # ignorar el filtro y traer todo.
        sentidos = [s for s in sentido if s in ('Entrante', 'Saliente')]
        filtros += f" AND i.[Sentido] IN ({_lista_sql(sentidos)})" if sentidos else " AND 1 = 0"
    # La duración es el tiempo hablado con el operador, que es lo que dura el audio: la
    # grabación no trae el IVR, la cola ni las esperas (medido: 175 s de audio para un
    # llamado de 171 s hablados + 81 s en espera).
    if duracion_min:
        filtros += f" AND i.[SegundosHablados] >= {int(duracion_min)}"
    if duracion_max:
        filtros += f" AND i.[SegundosHablados] <= {int(duracion_max)}"
    if not reauditar:
        filtros += " AND NOT EXISTS (SELECT 1 FROM [Acme].[calidad].[Auditorias] a WHERE a.[IdAplicativo] = i.[ConversationId])"
    # "Comentario" busca en lo que el cliente marcó en el IVR (tarjeta o número de cliente),
    # como las patentes/DNIs de Vantix, y en la nota de wrap-up (que hoy nadie carga).
    filtros += _filtro_comentario_like("CONCAT(i.[ExternalTag], N' ', i.[NotaWrapUp])", comentario)

    ctes_clause = f"""
    WITH benefix_tramos AS (
        SELECT
            i.[ConversationId], i.[InicioConversacion], i.[Sentido], i.[Operador],
            i.[Cola], i.[Skills], i.[Tipificacion], i.[NotaWrapUp], i.[TelefonoCliente],
            i.[SegundosHablados] AS Duracion,
            i.[SegundosEspera], i.[CantidadEsperas], i.[Transferido], i.[ExternalTag],
            CASE i.[DesconexionAgente]
                WHEN 'peer' THEN 'Cortó el cliente'
                WHEN 'client' THEN 'Cortó el operador'
                WHEN 'endpoint' THEN 'Cortó el operador'
                WHEN 'transfer' THEN 'Transferido'
                ELSE i.[DesconexionAgente]
            END AS MotivoFinalizacion,
            ROW_NUMBER() OVER (PARTITION BY i.[ConversationId]
                               ORDER BY i.[SegundosHablados] DESC, i.[InicioAgente]) AS TramoNumero
        FROM [Acme].[Benefix].[Interacciones] i
        WHERE {filtros}
    )
    """

    select_clause = """
        b.[ConversationId] AS [ID],
        b.[InicioConversacion] AS inicio,
        b.[Sentido] AS Sentido,
        b.[Operador] AS LoginId,
        b.[Cola] AS Cola,
        b.[Skills] AS Skill,
        b.[Tipificacion] AS [Tipificación],
        b.[Duracion] AS Duracion,
        b.[SegundosEspera] AS [Segundos en espera],
        b.[CantidadEsperas] AS [Cantidad de esperas],
        CASE WHEN b.[Transferido] = 1 THEN 'Sí' ELSE 'No' END AS Transferido,
        b.[MotivoFinalizacion] AS [Motivo de finalización],
        b.[TelefonoCliente] AS Telefono,
        b.[NotaWrapUp] AS Observaciones,
        b.[ExternalTag] AS externalTag
    """
    from_where_clause = "FROM benefix_tramos b WHERE b.TramoNumero = 1"

    query = construir_query_muestreo(select_clause, from_where_clause, cantidad, por_operador, por_tipificacion, "LoginId", "[Tipificación]", ctes_clause)
    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))
    return df


def get_filtered_data_Vantix(
    engine: Engine, 
    cantidad: int = 1, 
    idInteraccion: Union[list[str], None] = None, 
    Fecha_desde: Union[datetime, None] = None, 
    Fecha_hasta: Union[datetime, None] = None, 
    loginid: Union[list[str], None] = None, 
    duracion_min: Union[int, None] = None, 
    duracion_max: Union[int, None] = None, 
    empresa: Union[list[str], None] = None,
    cabezal: Union[list[str], None] = None, 
    campana: Union[list[str], None] = None,
    tipificacion: Union[list[str], None] = None,
    sentido: Union[list[str], None] = None,
    comentario: Union[list[str], None] = None,
    por_operador: bool = False,
    por_tipificacion: bool = False,
    omitir_limite: bool = False,
    reauditar: bool = False
) -> pd.DataFrame:
    """
    Obtiene y filtra llamadas de Orion (ContactCenter, vía linked server ORION_LINK)
    y las cruza con las grabaciones del recorder CYT (orion.Grabaciones).

    - La parte remota se ejecuta con OPENQUERY para que los filtros corran en el
      servidor de Orion (un JOIN con nombres de 4 partes arrastra las tablas enteras
      por el linked server).
    - El skill de cada llamada se deduce de HabilidadesPorAgenteLog: el/los skills
      en los que el agente estaba logueado al momento del llamado. `campana` recibe
      los skills (Habilidades de Orion) asignados a la campaña de auditoría y filtra
      por ese skill vigente.
    - El match llamada↔grabación usa agente + tarea + ventana temporal, eligiendo
      la grabación de duración más parecida (una por llamada).
    """

    def _sql_str(valor) -> str:
        """Escapa un valor para incrustarlo como literal SQL."""
        return "'" + str(valor).replace("'", "''") + "'"

    def _sql_in(valores) -> str:
        return ",".join(_sql_str(v) for v in valores)

    # --- Rango de fechas (el recorder retiene ~60 días; ese es el techo útil) ---
    fd_str = Fecha_desde.strftime('%Y-%m-%d') if Fecha_desde else None
    fh_str = Fecha_hasta.strftime('%Y-%m-%d') if Fecha_hasta else None
    if fd_str and not fh_str:
        fh_str = fd_str
    elif fh_str and not fd_str:
        fd_str = fh_str

    # Sin duplicados: los IDs se reparten en bloques (UNION ALL) más abajo y un ID
    # repetido en distintos bloques traería la misma llamada más de una vez.
    if idInteraccion:
        idInteraccion = list(dict.fromkeys(idInteraccion))

    # === Filtros que se ejecutan del lado remoto (Orion) ===
    filtros_remotos = ""
    if fd_str:
        filtros_remotos += f" AND e.Fecha >= {_sql_str(fd_str)} AND e.Fecha < DATEADD(DAY, 1, {_sql_str(fh_str)})"
    else:
        filtros_remotos += " AND e.Fecha >= DATEADD(DAY, -60, GETDATE())"
    # idInteraccion NO se agrega acá: una lista grande de IDs haría que el literal
    # de OPENQUERY supere el tope de 8000 chars de SQL Server. Se chunkea más abajo
    # generando un OPENQUERY por bloque de IDs, unidos con UNION ALL.
    if loginid:  # legajo del agente en Orion
        filtros_remotos += f" AND e.Agente IN ({_sql_in(loginid)})"
    if duracion_min:
        filtros_remotos += f" AND e.TiempoHablado >= {int(duracion_min)}"
    if duracion_max:
        filtros_remotos += f" AND e.TiempoHablado <= {int(duracion_max)}"
    if cabezal:  # tarea / cola operativa de Orion
        filtros_remotos += f" AND t.Nombre IN ({_sql_in(cabezal)})"
    if sentido:
        # El front manda Entrante/Saliente/Interno, pero en Orion (TiposLlamada) los
        # tipos son Entrante, Saliente y Discador (predictivo — también saliente); no
        # existe "Interno". Igual que el resto de los clientes (direccion_base), se
        # mapea el vocabulario del front al de Orion:
        #   Saliente -> Saliente + Discador (todas las salientes reales).
        #   Interno  -> sin equivalente en Orion (Vantix no tiene llamadas internas):
        #               pasa tal cual y no matchea => 0 resultados.
        direccion_base = {'Entrante': ['Entrante'], 'Saliente': ['Saliente', 'Discador']}
        dirs = [d for s in sentido for d in direccion_base.get(s, [s])]
        filtros_remotos += f" AND tl.Descripcion IN ({_sql_in(dirs)})"
    if comentario:
        # Filtro de comentario (OR + LIKE) sobre el COMENT (com.Valor). Corre del
        # lado remoto (Orion); las comillas se re-duplican al envolver el OPENQUERY.
        filtros_remotos += _filtro_comentario_like('com.Valor', comentario)
    if tipificacion:
        # Hay subcategorías cargadas con saltos de línea/espacios al final:
        # se compara contra la versión limpia (igual que en listar_tipificaciones).
        filtros_remotos += (
            " AND LTRIM(RTRIM(REPLACE(REPLACE(sub.Descripcion, CHAR(13), ''), CHAR(10), '')))"
            f" IN ({_sql_in(tipificacion)})"
        )
    if campana:
        # Skills de la campaña de auditoría: la llamada entra si el agente estaba
        # logueado en alguno de esos skills al momento del llamado.
        filtros_remotos += f"""
        AND EXISTS (
            SELECT 1
            FROM ContactCenter.dbo.HabilidadesPorAgenteLog hl2
            JOIN ContactCenter.dbo.Habilidades h2 ON h2.ID = hl2.HabilidadID
            WHERE hl2.AgenteID = e.Agente
              AND hl2.FechaInicio <= e.Fecha
              AND (hl2.FechaFin IS NULL OR hl2.FechaFin >= e.Fecha)
              AND h2.Nombre IN ({_sql_in(campana)})
        )"""

    def _query_remota(filtro_ids: str = "") -> str:
        return f"""
    SELECT
        e.ID_Llamada,
        e.Fecha,
        e.DuracionTotal,
        e.TiempoHablado,
        e.TiempoEnCola,
        COALESCE(NULLIF(e.TelefonoDiscado, ''), NULLIF(e.ANI, '')) AS Telefono,
        tl.Descripcion AS Sentido,
        e.Agente AS Legajo,
        a.Nombre_del_agente AS Agente,
        t.Nombre AS Tarea,
        e.MotivoCorte AS IdMotivoCorte,
        hsk.Skills AS SkillAgente,
        LTRIM(RTRIM(REPLACE(REPLACE(cat.Descripcion, CHAR(13), ''), CHAR(10), ''))) AS Categoria,
        LTRIM(RTRIM(REPLACE(REPLACE(sub.Descripcion, CHAR(13), ''), CHAR(10), '')))  AS Subcategoria,
        com.Valor AS Comentario
    FROM ContactCenter.dbo.EstadPorLlamada e
    JOIN ContactCenter.dbo.Agentes a ON a.Legajo = e.Agente
    LEFT JOIN ContactCenter.dbo.Tareas t ON t.ID = e.Campaign
    LEFT JOIN ContactCenter.dbo.TiposLlamada tl ON tl.Tipo = e.Tipo
    LEFT JOIN ContactCenter.dbo.DatosPorLlamada dps
        ON dps.Call_Id = e.ID_Llamada AND dps.Clave = 'CODSUBCAT'
    LEFT JOIN ContactCenter.dbo.SubCategorias sub
        ON sub.CodSubcategoria = COALESCE(TRY_CAST(dps.Valor AS INT), NULLIF(e.Codificacion, 0))
    LEFT JOIN ContactCenter.dbo.Categorias cat ON cat.CodCategoria = sub.CodCategoria
    LEFT JOIN ContactCenter.dbo.DatosPorLlamada com
        ON com.Call_Id = e.ID_Llamada AND com.Clave = 'COMENT'
    CROSS APPLY (
        SELECT STUFF((
            SELECT ', ' + h.Nombre
            FROM ContactCenter.dbo.HabilidadesPorAgenteLog hl
            JOIN ContactCenter.dbo.Habilidades h ON h.ID = hl.HabilidadID
            WHERE hl.AgenteID = e.Agente
              AND hl.FechaInicio <= e.Fecha
              AND (hl.FechaFin IS NULL OR hl.FechaFin >= e.Fecha)
            FOR XML PATH('')), 1, 2, '') AS Skills
    ) hsk
    WHERE e.Agente <> 0
      AND e.TiempoHablado > 0
      AND e.Grabada = 1
      {filtros_remotos}{filtro_ids}
    """

    def _openquery(remota: str) -> str:
        return "SELECT * FROM OPENQUERY(ORION_LINK, '" + remota.replace("'", "''") + "')"

    # OPENQUERY sólo admite un literal de hasta 8000 chars. Si llegan muchos IDs,
    # los repartimos en bloques y unimos un OPENQUERY por bloque con UNION ALL: así
    # cada literal queda bajo el tope y el filtro de IDs sigue corriendo en Orion.
    if idInteraccion:
        TOPE_LITERAL = 7000  # margen bajo el tope real de 8000
        base_len = len(_query_remota(" AND CAST(e.ID_Llamada AS VARCHAR(50)) IN ()").replace("'", "''"))
        presupuesto = max(TOPE_LITERAL - base_len, 500)

        bloques: list[list[str]] = []
        actual: list[str] = []
        largo = 0
        for _id in idInteraccion:
            tok_len = len(_sql_str(_id).replace("'", "''")) + 1  # +1 por la coma
            if actual and largo + tok_len > presupuesto:
                bloques.append(actual)
                actual, largo = [], 0
            actual.append(_id)
            largo += tok_len
        if actual:
            bloques.append(actual)

        rem_source = "\n        UNION ALL\n        ".join(
            _openquery(_query_remota(f" AND CAST(e.ID_Llamada AS VARCHAR(50)) IN ({_sql_in(b)})"))
            for b in bloques
        )
    else:
        rem_source = _openquery(_query_remota())

    rango_grab = (
        f"WHERE g.Fecha BETWEEN {_sql_str(fd_str)} AND {_sql_str(fh_str)}"
        if fd_str else
        "WHERE g.Fecha >= DATEADD(DAY, -60, GETDATE())"
    )

    # --- Motivo de finalización (quién cortó) ---
    # La causa de corte fina vive en la base MySQL de Orion (omnicanalidad), no en
    # ContactCenter: el MotivoCorte de EstadPorLlamada sólo distingue "Agente corta"
    # de "Desconexión". Cada tramo de agente (EventType 2/5) trae su EventCause, que
    # el catálogo completioncauses traduce a "Desconexión Local" (corta el operador),
    # "Desconexión" (corta el cliente), etc. Es la misma fuente que alimenta el ETL de
    # orion.Silver_Llamadas_Detalle, pero leída en vivo: ese ETL corre una vez por día
    # y dejaría sin motivo a las llamadas del día en curso.
    # El rango se abre 2 días para no perder el tramo de llamadas cerca de medianoche;
    # el match real es por ID de llamada + legajo, no por fecha.
    if fd_str:
        rango_motivo = (
            f"e.EventStartTime >= '{fd_str} 00:00:00'"
            f" AND e.EventStartTime < DATE_ADD('{fh_str} 00:00:00', INTERVAL 2 DAY)"
        )
    else:
        rango_motivo = "e.EventStartTime >= DATE_SUB(NOW(), INTERVAL 61 DAY)"

    query_motivo = f"""
        SELECT CAST(e.GlobalId AS CHAR) AS GlobalId,
               e.Agente AS Legajo,
               e.EventStartTime,
               e.EventCause,
               CAST(cc.Nombre AS CHAR) AS Motivo
        FROM omnicanalidad.eventdetailrecords e
        LEFT JOIN omnicanalidad.completioncauses cc ON cc.CauseID = e.EventCause
        WHERE e.EventType IN (2,5)
          AND e.Agente IS NOT NULL AND e.Agente <> 0
          AND {rango_motivo}
    """

    ctes_clause = f"""
    WITH rem AS (
        {rem_source}
    ),
    grab AS (
        SELECT g.ID,
               CAST(g.Fecha AS DATETIME) + CAST(g.Hora AS DATETIME) AS ts,
               g.Duracion,
               age.Nombre_Agente,
               cam.Nombre_Campana
        FROM orion.Grabaciones g
        JOIN orion.Agentes age ON age.Id_Agente = g.Agente
        JOIN orion.Campanas cam ON cam.Id_Campana = g.Campana
        {rango_grab}
    ),
    motivos AS (
        SELECT * FROM OPENQUERY(MYSQL_LINK, '{query_motivo.replace("'", "''")}')
    )
    """

    select_clause = """
        CAST(r.ID_Llamada AS VARCHAR(50)) AS [ID del llamado],
        CAST(r.Fecha AS DATE) AS [Fecha del llamado],
        CAST(r.Fecha AS TIME) AS [hora del llamado],
        r.Fecha AS [Fecha y hora del llamado],
        r.TiempoHablado AS [Duracion],
        'Vantix' AS [Cliente empresaria],
        r.SkillAgente AS [Skill],
        r.Tarea AS [Campaña dentro del cliente],
        r.DuracionTotal AS [Duracion del llamado total],
        r.TiempoEnCola AS [Tiempo en Cola],
        r.Sentido AS [Sentido del llamado],
        r.Categoria AS [Tipificacion/Categoria],
        r.Subcategoria AS [Subtipificacion/Subcategoria],
        r.Agente AS [Agente],
        r.Legajo AS [LoginId],
        r.Telefono AS [Numero de telefono del cliente],
        CASE
            WHEN fin.EventCause = 5 THEN N'Cortó el operador'
            WHEN fin.EventCause = 6 THEN N'Cortó el cliente'
            WHEN fin.EventCause = 1 THEN N'Cortó el cliente (sin atender)'
            WHEN fin.EventCause IN (111, 112, 113) THEN N'El operador transfirió'
            -- Respaldo: cuando la llamada entra por IVR, Orion deja el tramo del agente
            -- con EventCause 0 ("Desconocida") y la causa fina se pierde (16% de las
            -- llamadas auditables). Ahí ContactCenter sí distingue quién cortó, y donde
            -- las dos fuentes tienen dato coinciden en el 99.8% ("Agente corta" <->
            -- Desconexión Local, "Desconexión" <-> Desconexión).
            WHEN r.IdMotivoCorte = 5 THEN N'Cortó el operador'
            WHEN r.IdMotivoCorte = 6 THEN N'Cortó el cliente'
            WHEN fin.EventCause = 0 THEN N'Sin determinar'
            ELSE fin.Motivo COLLATE DATABASE_DEFAULT
        END AS [Motivo de finalización],
        r.Comentario AS [Comentario del Agente],
        g.ID AS [ID de la grabación]
    """

    # Match llamada↔grabación: mismo agente, misma tarea, la grabación arranca
    # dentro de la llamada (con 60s de tolerancia) y se elige la de duración
    # más parecida al tiempo hablado. CROSS APPLY = sólo llamadas con grabación.
    from_where_clause = """
    FROM rem r
    CROSS APPLY (
        SELECT TOP 1 g.ID
        FROM grab g
        WHERE g.Nombre_Agente COLLATE DATABASE_DEFAULT = r.Agente COLLATE DATABASE_DEFAULT
          AND g.Nombre_Campana COLLATE DATABASE_DEFAULT = r.Tarea COLLATE DATABASE_DEFAULT
          AND g.ts BETWEEN DATEADD(SECOND, -60, r.Fecha)
                       AND DATEADD(SECOND, r.DuracionTotal, r.Fecha)
        ORDER BY ABS(g.Duracion - r.TiempoHablado),
                 ABS(DATEDIFF(SECOND, r.Fecha, g.ts))
    ) g
    OUTER APPLY (
        SELECT TOP 1 m.EventCause, m.Motivo
        FROM motivos m
        WHERE m.GlobalId COLLATE DATABASE_DEFAULT = CAST(r.ID_Llamada AS VARCHAR(50)) COLLATE DATABASE_DEFAULT
          AND m.Legajo = r.Legajo
          AND m.EventStartTime BETWEEN DATEADD(SECOND, -60, r.Fecha)
                                   AND DATEADD(SECOND, r.DuracionTotal + 60, r.Fecha)
        ORDER BY ABS(DATEDIFF(SECOND, r.Fecha, m.EventStartTime))
    ) fin
    WHERE 1=1
    """

    if not reauditar:
        from_where_clause += """
        AND NOT EXISTS (
            SELECT 1 FROM [Acme].calidad.Auditorias aud
            WHERE aud.IdAplicativo = CAST(r.ID_Llamada AS VARCHAR(50))
        )"""

    # Muestreo: particionamos por Agente y Subcategoría si el usuario lo requiere
    query = construir_query_muestreo(
        select_clause=select_clause,
        from_where_clause=from_where_clause,
        cantidad=cantidad,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        col_operador="[Agente]",
        col_tipificacion="[Subtipificacion/Subcategoria]",
        ctes_clause=ctes_clause
    )

    df = pd.read_sql(query, engine)

    if (por_operador or por_tipificacion) and len(df) > 200 and not omitir_limite:
        raise AuditLimitExceededError(len(df))

    return df