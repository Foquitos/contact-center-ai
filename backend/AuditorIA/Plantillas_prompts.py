from typing import Any, Dict, List, Optional, Union
import logging
import pandas as pd
import json
from sqlalchemy import create_engine, text, exc as sqlalchemy_exc, Engine
from app.models import PlantillasIA
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
from AuditorIA import limites_texto
from AuditorIA import senales_prompt
from AuditorIA import conocimiento_plantilla

logger = logging.getLogger(__name__)

class plantillas_manager():
    def __init__(self, engine: Engine):
        self.engine = engine

    #*Get
    def obtener_plantilla_para_IA(self, plantilla_id: int) -> PlantillasIA:
        """Devuelve un diccionario con las plantillas de prompts para auditoría IA.
        Returns:
            Dict: Diccionario con las plantillas de prompts que contiene las siguientes claves:
            - 'system': Instrucciones de sistema para el auditor IA.
            - 'text': Instrucciones para el auditor IA.
            - 'response_schema': Esquema de respuesta para la auditoría.
            
    """

        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""Exec calidad.sp_ObtenerPlantillaParaIA
                        @TargetPlantillaID = :plantilla_id"""),
                    {"plantilla_id": plantilla_id}
                )
                plantilla = result.fetchone()
                if plantilla is None:
                    raise ValueError(f"No se encontró la plantilla con id {plantilla_id}")
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        plantilla = plantilla._asdict()
        response = PlantillasIA(
            system_prompts=plantilla['SystemPrompt'],
            text=plantilla['Prompt'],
            response_schema=plantilla['ResponseSchema'],
            modelo=plantilla.get('ModeloIA') or MODELO_IA_DEFAULT,
            # La columna la agrega la migración 2026-08-19b. Si todavía no se
            # aplicó, el SP no devuelve la clave y queda None -> default.
            nivel_razonamiento=plantilla.get('NivelRazonamiento'),
        )
        return response

    def obtener_plantilla(self, plantilla_id: int) -> Optional[Dict[str, Any]]:
        """Devuelve un diccionario con las plantillas de prompts para poder ser visualizadas por el usuario.
        Returns:
            Dict: Diccionario con las plantillas de prompts que contiene las siguientes claves:
            - 'id': ID de la plantilla.
            - 'nombre': Nombre de la plantilla.
            - 'descripcion': Descripción de la plantilla.
            - 'system': Instrucciones de sistema para el auditor IA.
            - 'recordatorio': Recordatorio para el auditor IA.
            - 'atributos': Lista de atributos en forma de diccionario de la plantilla.
            - 'DarAviso': Indica si se debe alertar al usuario si el atributo cumple ciertas condiciones.
            - 'FrasesAviso': Palabras clave separadas por comas que, si se encuentran en la respuesta del auditor IA para ese atributo, disparan una alerta al usuario.
    """

        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""EXEC calidad.sp_ObtenerPlantillaCompleta 
                            @TargetPlantillaID = :plantilla_id"""),
                    {"plantilla_id": plantilla_id}
                )
                
                # fetchone() devuelve una fila o None si no hay resultados
                row = result.fetchone() 
                
                # Comprobamos si se encontró una fila antes de intentar acceder a ella
                if row:
                    json_string = row[0]
                    return json.loads(json_string)
                else:
                    # Si no hay fila, la plantilla no existe. Devolvemos None.
                    return None

            except sqlalchemy_exc.SQLAlchemyError as e:
                # Puedes registrar el error aquí si quieres
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def atributos_de_plantilla(self, plantilla_id: int,
                               incluir_inactivos: bool = False) -> Dict[int, Dict[str, Any]]:
        """{AtributoID: {nombre, orden, DarAviso, FrasesAviso, activo}} de la plantilla.

        Devuelve también los campos que NO forman parte del snapshot de una versión
        (orden y las alertas por mail, ver AuditorIA/versionado.py::CAMPOS_ATRIBUTO):
        al restaurar una versión hay que reenviarlos tal como están hoy para no
        moverlos sin querer.

        `incluir_inactivos` es lo que hace posible restaurar: un atributo que una
        revisión dio de baja sigue en la tabla con IsActive = 0 y hay que poder
        encontrarlo para revivirlo.
        """
        filtro = "" if incluir_inactivos else " AND IsActive = 1"
        with self.engine.begin() as connection:
            try:
                filas = connection.execute(
                    text(f"""SELECT AtributoID, NombreAtributo, Orden, DarAviso, FrasesAviso, IsActive
                             FROM calidad.Atributos
                             WHERE PlantillaID = :plantilla_id{filtro}"""),
                    {"plantilla_id": plantilla_id},
                ).fetchall()
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        return {
            int(fila[0]): {
                "nombre": fila[1], "orden": fila[2],
                "DarAviso": bool(fila[3]) if fila[3] is not None else False,
                "FrasesAviso": fila[4], "activo": bool(fila[5]),
            }
            for fila in filas
        }

    def atributos_activos(self, plantilla_id: int) -> Dict[int, Dict[str, Any]]:
        """Solo los atributos ACTIVOS (ver `atributos_de_plantilla`).

        La usa la revisión integral con IA (`/plantillas/{id}/ia/aplicar-revision`) para
        dos cosas: validar que cada AtributoID del body pertenece a ESTA plantilla antes
        de modificarlo o darlo de baja —el alcance por empresa se valida sobre la
        plantilla del path, así que sin este chequeo un id ajeno colado en el body se
        tocaría igual— y saber en qué orden van los atributos nuevos.
        """
        return self.atributos_de_plantilla(plantilla_id, incluir_inactivos=False)

    def reactivar_atributo(self, atributo_id: int, plantilla_id: int) -> None:
        """Revive un atributo dado de baja (IsActive = 1).

        Es la contracara de `desactivar_atributo` y existe para poder RESTAURAR una
        versión de la plantilla: si una revisión eliminó un atributo, volver atrás
        significa revivir esa misma fila (no crear otra), para no perder el enlace con
        las auditorías que ya lo respondieron. Va por UPDATE directo porque no hay un SP
        para esto, igual que Ponderacion/EsOpcional. Se acota por PlantillaID para que un
        id ajeno no pueda revivirse desde otra plantilla.
        """
        with self.engine.begin() as connection:
            try:
                connection.execute(
                    text("""UPDATE calidad.Atributos SET IsActive = 1
                            WHERE AtributoID = :atributo_id AND PlantillaID = :plantilla_id"""),
                    {"atributo_id": atributo_id, "plantilla_id": plantilla_id},
                )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def empresas_disponibles(self, user_permissions: List[str], is_super_admin: bool) -> Dict[int, str]:
        """Devuelve un diccionario con las empresas disponibles según los permisos del usuario.
        Returns:
            Dict[int, str]: Diccionario con id como clave y nombre de la empresa como valor.
        """
        empresas = {}
        
        # Permiso maestro que tienen los de Calidad para ver todo
        PERMISO_VER_TODO = 'templates:manage' 
        
        can_see_all = is_super_admin or (PERMISO_VER_TODO in user_permissions)

        with self.engine.begin() as connection:
            try:
                # Traemos también el código del permiso requerido.
                # IsActive = 1: las empresas dadas de baja (soft delete, ver
                # calidad.sp_DesactivarEmpresa) no se ofrecen para plantillas.
                result = connection.execute(
                    text("""
                    SELECT
                        e.EmpresaID as id,
                        e.Nombre as Empresa,
                        p.code as required_permission_code
                    FROM [Acme].[calidad].[Empresas] e
                    LEFT JOIN [Acme].[pagina_web].[Permissions] p ON e.RequiredPermissionID = p.id
                    WHERE e.IsActive = 1
                    ORDER BY e.Nombre
                    """)
                )
                
                for row in result.fetchall():
                    # Lógica de Filtrado
                    required_perm = row.required_permission_code
                    
                    # 1. Si es SuperAdmin o tiene permiso maestro (Calidad), ve todo.
                    if can_see_all:
                        empresas[row.id] = row.Empresa
                        continue
                        
                    # 2. Si la empresa NO requiere permiso (es pública), la mostramos.
                    if required_perm is None:
                        empresas[row.id] = row.Empresa
                        continue
                        
                    # 3. Si requiere permiso, verificamos si el usuario lo tiene.
                    if required_perm in user_permissions:
                        empresas[row.id] = row.Empresa

            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        return empresas

    def campanas_disponibles (self, empresa_id: int) -> Dict[int, str]:
        """Devuelve un diccionario con las campañas disponibles.
        Returns:
            Dict[int, str]: Diccionario con id como clave y nombre de la campaña como valor.
        """
        campanas = {}
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""SELECT
                    c.Nombre Campaña,
                    c.CampanaID id
                    FROM [Acme].[calidad].[Campanas] c
                    where EmpresaID = :empresa_id and c.IsActive = 1
                    order by c.Nombre"""),
                    {"empresa_id": empresa_id}
                )
                for row in result.fetchall():
                    campanas[row.id] = row.Campaña
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        return campanas

    def listar_plantillas(self, campana_id: int) -> Dict[int, str]:
        """Devuelve un diccionario con las plantillas de prompts disponibles para una campaña.
        Las claves son los IDs de las plantillas y los valores son los nombres de las plantillas.
        Returns:
            Dict[int, str]: Diccionario con PlantillaID como clave y NombrePlantilla como valor.
        """
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                text("""Exec calidad.sp_ListarPlantillasPorCampana
                    @TargetCampanaID = :campana_id"""),
                {"campana_id": campana_id}
                )
                plantillas_rows = result.fetchall()
                if not plantillas_rows:
                    return {}
                # Asumiendo que la primera columna es PlantillaID y la segunda es NombrePlantilla
                return {row[0]: row[1] for row in plantillas_rows}
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def obtener_uso_plantillas(self, campana_id: int) -> List[Dict[str, Any]]:
        """Devuelve el listado de plantillas activas de una campaña con estadísticas de uso
        en los últimos meses y estado de actividad (código de colores / semáforo).

        Permite identificar plantillas sin utilizar para su evaluación o eliminación.
        """
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""
                    SELECT 
                        p.PlantillaID,
                        p.Nombre,
                        p.FechaCreacion,
                        COUNT(a.AuditoriaID) AS total_auditorias,
                        SUM(CASE WHEN a.FechaAuditoria >= DATEADD(day, -30, GETDATE()) THEN 1 ELSE 0 END) AS auditorias_ultimos_30d,
                        SUM(CASE WHEN a.FechaAuditoria >= DATEADD(day, -60, GETDATE()) THEN 1 ELSE 0 END) AS auditorias_ultimos_60d,
                        SUM(CASE WHEN a.FechaAuditoria >= DATEADD(day, -90, GETDATE()) THEN 1 ELSE 0 END) AS auditorias_ultimos_90d,
                        SUM(CASE WHEN a.FechaAuditoria >= DATEADD(day, -180, GETDATE()) THEN 1 ELSE 0 END) AS auditorias_ultimos_180d,
                        MAX(a.FechaAuditoria) AS ultima_auditoria,
                        DATEDIFF(day, MAX(a.FechaAuditoria), GETDATE()) AS dias_desde_ultima
                    FROM calidad.Plantillas p
                    LEFT JOIN calidad.Auditorias a 
                        ON a.PlantillaID = p.PlantillaID 
                        AND a.IsActive = 1
                    WHERE p.CampanaID = :campana_id 
                      AND p.IsActive = 1
                    GROUP BY p.PlantillaID, p.Nombre, p.FechaCreacion
                    ORDER BY p.Nombre ASC
                    """),
                    {"campana_id": campana_id}
                )
                rows = result.fetchall()
                plantillas_uso = []
                for row in rows:
                    total = int(row.total_auditorias or 0)
                    u30 = int(row.auditorias_ultimos_30d or 0)
                    u60 = int(row.auditorias_ultimos_60d or 0)
                    u90 = int(row.auditorias_ultimos_90d or 0)
                    u180 = int(row.auditorias_ultimos_180d or 0)
                    ultima = row.ultima_auditoria
                    dias = row.dias_desde_ultima

                    if total == 0 or ultima is None:
                        estado_uso = "sin_uso"
                        estado_color = "danger"
                        estado_label = "Sin auditorías"
                    elif dias is not None and dias <= 30:
                        estado_uso = "activa"
                        estado_color = "success"
                        estado_label = "En uso activo"
                    elif dias is not None and dias <= 90:
                        estado_uso = "inactiva_1m"
                        estado_color = "warning"
                        estado_label = "Sin uso > 1 mes"
                    else:
                        estado_uso = "inactiva_3m"
                        estado_color = "danger"
                        estado_label = "Sin uso > 3 meses"

                    plantillas_uso.append({
                        "plantilla_id": int(row.PlantillaID),
                        "nombre": str(row.Nombre or ""),
                        "fecha_creacion": row.FechaCreacion.isoformat() if row.FechaCreacion else None,
                        "total_auditorias": total,
                        "auditorias_ultimos_30d": u30,
                        "auditorias_ultimos_60d": u60,
                        "auditorias_ultimos_90d": u90,
                        "auditorias_ultimos_180d": u180,
                        "ultima_auditoria": ultima.isoformat() if ultima else None,
                        "dias_desde_ultima": int(dias) if dias is not None else None,
                        "estado_uso": estado_uso,
                        "estado_color": estado_color,
                        "estado_label": estado_label,
                    })
                return plantillas_uso
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")


    def skill_disponibles (self, Empresa_id: int) -> List[str]:
        """Devuelve un DataFrame con las skills disponibles para una empresa.
        Returns:
            pd.DataFrame: DataFrame con las skills disponibles que contiene las siguientes columnas:
            - 'id': ID de la skill.
            - 'nombre': Nombre de la skill.
        """
        skills = []
        
        with self.engine.begin() as connection:
            try:
                response = connection.execute(text("""select distinct p.nombre [Nombre_plataforma] from calidad.Campanas c
                join calidad.Plataformas p on c.PlataformaID = p.PlataformaID
                where EmpresaID= :empresa_id and c.IsActive = 1"""), {"empresa_id": Empresa_id,})
                lista_plataformas = [row[0] for row in response.fetchall()]
                
                if 'Mitrol' in lista_plataformas:
                    response = connection.execute(text("""SELECT DISTINCT 
                        Campaña AS skill
                    FROM 
                        [Acme].[dbo].[detalle_de_interacciones_por_campana_lote]
                    WHERE 
                        fecha_inicio > DATEADD(Day, -30, GETDATE())
                        AND empresa IN (
                            -- La consulta ahora solo busca los nombres autorizados
                            -- en tu tabla de mapeo para ese EmpresaID.
                            SELECT nombre_mitrol 
                            FROM calidad.Empresa_nombreMitrol
                            WHERE EmpresaID = :EmpresaIDBuscada
                        )
                    ORDER BY 
                        Campaña;"""), {"EmpresaIDBuscada": Empresa_id,})
                    skills = [row[0] for row in response.fetchall()]
                if 'Avaya' in lista_plataformas:
                    pass  # Implementar lógica para Avaya si es necesario
                if 'Genesys' in lista_plataformas:
                    # Colas de Genesys (Benefix) de los últimos 30 días atendidas por
                    # operadores de Acme: mismo criterio que get_filtered_data_Benefix
                    # (SQL_query.BENEFIX_OPERADOR_ACME).
                    response = connection.execute(text("""SELECT DISTINCT i.[Cola] AS skill
                        FROM [Acme].[Benefix].[Interacciones] i
                        WHERE i.[Fecha] > DATEADD(Day, -30, GETDATE())
                          AND i.[Canal] = 'voice' AND i.[Cola] IS NOT NULL
                          AND (i.[Operador] LIKE N'ACME - %' OR i.[Email] LIKE N'%@consultores.benefix.example')
                        ORDER BY i.[Cola]"""))
                    skills = [row[0] for row in response.fetchall()]
                if 'CXOne' in lista_plataformas:
                    response = connection.execute(text("""SELECT DISTINCT 
                        skillName AS skill
                    FROM 
                        [Acme].ALARMIX.Grabaciones
                    WHERE 
                        segmentContactStartTime > DATEADD(Day, -30, GETDATE()) and skillName is not null
                    ORDER BY 
                        skillName"""))
                    skills = [row[0] for row in response.fetchall()]
                if 'Wize' in lista_plataformas:
                    response = connection.execute(text("""SELECT distinct 
                            [grupo_atencion] as skill
                        FROM [Acme].[dbo].[Vitalis_Llamadas]
                        where agente_usuario like '% Acme' 
                        and fecha_inicio > DATEADD(Day, -30, GETDATE()) and [grupo_atencion] is not null
                        order by [grupo_atencion]"""))
                    skills = [row[0] for row in response.fetchall()]
                if 'Orion' in lista_plataformas:
                    # Skills reales de Orion (Habilidades). Se excluyen los
                    # placeholders de fábrica ('habilidadNN'). El skill de cada
                    # llamada se deduce del log de habilidades por agente
                    # (ver get_filtered_data_Vantix).
                    query = """
                    SELECT skill FROM OPENQUERY(ORION_LINK, '
                        SELECT h.Nombre AS skill
                        FROM ContactCenter.dbo.Habilidades h
                        WHERE h.Nombre NOT LIKE ''habilidad%''
                    ') AS oq
                    ORDER BY skill;
                    """

                    response = connection.execute(text(query))
                    skills = [row[0] for row in response.fetchall()]
                if 'AsterVoIP' in lista_plataformas:
                    response = connection.execute(text("""SELECT DISTINCT
                            [Cola Entrante] AS skill
                        FROM [Acme].[Farmalux].[Grabaciones]
                        WHERE [Fecha/Hora] > DATEADD(Day, -30, GETDATE())
                          AND [Cola Entrante] IS NOT NULL AND [Cola Entrante] <> ''
                        ORDER BY [Cola Entrante]"""))
                    skills = [row[0] for row in response.fetchall()]
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        
        return skills

    def skill_asignados (self, campana_id: int) -> List[str]:
        """Devuelve una lista con las skills asignadas a una campaña.
        Returns:
            List[str]: Lista con los nombres de las skills asignadas.
        """
        skills = []
        
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""Exec calidad.sp_ListarSkillsPorCampana
                        @TargetCampanaID = :campana_id"""),
                    {"campana_id": campana_id}
                )
                skills = [row[0] for row in result.fetchall()]
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        
        return skills

    def plataformas_disponibles (self) -> Dict[int, str]:
        """Devuelve una lista con las plataformas disponibles.
        Returns:
            Dict[int, str]: Diccionario con id como clave y nombre de la plataforma como valor.
        """
        plataformas = {}
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""SELECT 
                    p.PlataformaID AS id,
                    p.Nombre AS nombre
                    FROM [Acme].[calidad].[Plataformas] p
                    order by p.Nombre""")
                )
                plataformas = {row.id: row.nombre for row in result.fetchall()}
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        return plataformas

    def listar_tipificaciones(self, campana_id: int) -> Union[List[str], Dict[str, Any]]: # Modificamos el tipo de retorno
        """Devuelve una lista o árbol de tipificaciones según la plataforma."""
        tipificaciones = []
        
        with self.engine.begin() as connection:
            
            response = connection.execute(text("""
                select Plataformas.nombre Nombre_plataforma
                from calidad.Campanas
                join calidad.Plataformas
                on Campanas.PlataformaID = Plataformas.PlataformaID
                where Campanas.IsActive = 1 and CampanaID = :campana_id;"""), {"campana_id": campana_id,})
            plataforma = response.fetchone()
            if plataforma is None:
                raise ValueError(f"No se encontró la campaña con id {campana_id}")
            plataforma_nombre = plataforma[0]
            
            try:
                if plataforma_nombre in ('Mitrol', 'Wize', 'Orion', 'Genesys', 'AsterVoIP'):
                    # --- NUEVA LÓGICA DE ÁRBOL PARA MITROL ---
                    if plataforma_nombre == 'Mitrol':
                        query_arbol = text("""
                            WITH DatosNormalizados AS (
                                SELECT 
                                    d.Tipificación,
                                    CAST('<x>' + REPLACE(REPLACE(d.Tipificación, ' -> ', '</x><x>'), ' - ', '</x><x>') + '</x>' AS XML) AS xml_data
                                FROM detalle_de_interacciones_por_campana_lote d
                                JOIN calidad.Skills s 
                                    ON s.Nombre COLLATE Latin1_General_CS_AS = d.Campaña -- Aquí se fuerza el Case-Sensitive
                                    AND s.IsActive = 1
                                WHERE fecha_inicio > DATEADD(Day, -10, GETDATE())
                                and s.CampanaID = :campana_id
                            )
                            SELECT DISTINCT 
                                Tipificación,
                                xml_data.value('/x[1]', 'VARCHAR(150)') AS Nivel_1,
                                xml_data.value('/x[2]', 'VARCHAR(150)') AS Nivel_2,
                                xml_data.value('/x[3]', 'VARCHAR(150)') AS Nivel_3,
                                xml_data.value('/x[4]', 'VARCHAR(150)') AS Nivel_4,
                                xml_data.value('/x[5]', 'VARCHAR(150)') AS Nivel_5,
                                xml_data.value('/x[6]', 'VARCHAR(150)') AS Nivel_6
                            FROM DatosNormalizados
                            ORDER BY Tipificación
                        """)
                        
                        result = connection.execute(query_arbol, {"campana_id": campana_id})
                    elif plataforma_nombre == 'Wize':
                        query_arbol = text("""WITH DatosNormalizados AS (
                                SELECT 
                                    d.[subtipo_interaccion],
                                    CAST('<x>' + REPLACE(REPLACE(d.[subtipo_interaccion], ' -> ', '</x><x>'), ' - ', '</x><x>') + '</x>' AS XML) AS xml_data
                                FROM [Vitalis_Llamadas] d
                                WHERE fecha_inicio > DATEADD(Day, -10, GETDATE()) and agente_usuario like '% Acme' 
                            )
                            SELECT DISTINCT 
                                [subtipo_interaccion],
                                xml_data.value('/x[1]', 'VARCHAR(150)') AS Nivel_1,
                                xml_data.value('/x[2]', 'VARCHAR(150)') AS Nivel_2,
                                xml_data.value('/x[3]', 'VARCHAR(150)') AS Nivel_3,
                                xml_data.value('/x[4]', 'VARCHAR(150)') AS Nivel_4,
                                xml_data.value('/x[5]', 'VARCHAR(150)') AS Nivel_5,
                                xml_data.value('/x[6]', 'VARCHAR(150)') AS Nivel_6
                            FROM DatosNormalizados
                            ORDER BY [subtipo_interaccion];""")
                        result = connection.execute(query_arbol)
                    elif plataforma_nombre == 'Genesys':
                        # Wrap-ups de Benefix ("P&B - Consulta de Saldo"): el nivel 1 es
                        # la línea de producto. El '&' se escapa antes del CAST a XML o
                        # la conversión falla con "illegal name character". Algunos
                        # códigos vienen sin espacio antes del guion ("MDF- Solicitud de
                        # Evento"): se corta por '- ' (tras juntar ' - ' en '- ') para
                        # que queden dentro de su grupo y no como un grupo aparte.
                        query_arbol = text("""
                            WITH DatosNormalizados AS (
                                SELECT
                                    i.[Tipificacion],
                                    CAST('<x>' + REPLACE(REPLACE(REPLACE(REPLACE(i.[Tipificacion], '&', '&amp;'), '<', '&lt;'), ' - ', '- '), '- ', '</x><x>') + '</x>' AS XML) AS xml_data
                                FROM [Acme].[Benefix].[Interacciones] i
                                JOIN calidad.Skills s
                                    ON s.Nombre = i.[Cola] AND s.IsActive = 1
                                WHERE i.[Fecha] > DATEADD(Day, -10, GETDATE())
                                  AND i.[Canal] = 'voice' AND i.[Tipificacion] IS NOT NULL
                                  AND s.CampanaID = :campana_id
                            )
                            SELECT DISTINCT
                                Tipificacion,
                                xml_data.value('/x[1]', 'NVARCHAR(150)') AS Nivel_1,
                                xml_data.value('/x[2]', 'NVARCHAR(150)') AS Nivel_2,
                                xml_data.value('/x[3]', 'NVARCHAR(150)') AS Nivel_3,
                                xml_data.value('/x[4]', 'NVARCHAR(150)') AS Nivel_4,
                                xml_data.value('/x[5]', 'NVARCHAR(150)') AS Nivel_5,
                                xml_data.value('/x[6]', 'NVARCHAR(150)') AS Nivel_6
                            FROM DatosNormalizados
                            ORDER BY Tipificacion
                        """)
                        result = connection.execute(query_arbol, {"campana_id": campana_id})
                    elif plataforma_nombre == 'Orion':
                        # Árbol Categoría -> Subcategoría de las llamadas de los
                        # últimos 10 días (igual que Mitrol: oculta tipificaciones
                        # históricas que ya no se usan) atendidas por agentes
                        # logueados en los skills (Habilidades) asignados a la
                        # campaña. La parte remota corre en Orion vía OPENQUERY;
                        # el cruce con calidad.Skills se hace local.
                        query_arbol = text("""
                            WITH rem AS (
                                SELECT * FROM OPENQUERY(ORION_LINK, '
                                    SELECT DISTINCT
                                        h.Nombre AS Skill,
                                        LTRIM(RTRIM(REPLACE(REPLACE(cat.Descripcion, CHAR(13), ''''), CHAR(10), '''')))  AS Categoria,
                                        LTRIM(RTRIM(REPLACE(REPLACE(sub.Descripcion, CHAR(13), ''''), CHAR(10), '''')))  AS Subcategoria
                                    FROM ContactCenter.dbo.EstadPorLlamada e
                                    JOIN ContactCenter.dbo.DatosPorLlamada d
                                        ON d.Call_Id = e.ID_Llamada AND d.Clave = ''CODSUBCAT''
                                    JOIN ContactCenter.dbo.SubCategorias sub
                                        ON sub.CodSubcategoria = TRY_CAST(d.Valor AS INT)
                                    JOIN ContactCenter.dbo.Categorias cat
                                        ON cat.CodCategoria = sub.CodCategoria
                                    JOIN ContactCenter.dbo.HabilidadesPorAgenteLog hl
                                        ON hl.AgenteID = e.Agente
                                       AND hl.FechaInicio <= e.Fecha
                                       AND (hl.FechaFin IS NULL OR hl.FechaFin >= e.Fecha)
                                    JOIN ContactCenter.dbo.Habilidades h ON h.ID = hl.HabilidadID
                                    WHERE e.Fecha > DATEADD(DAY, -10, GETDATE())
                                ') AS oq
                            )
                            SELECT DISTINCT
                                r.Subcategoria,
                                r.Categoria AS Nivel_1,
                                r.Subcategoria AS Nivel_2,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_3,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_4,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_5,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_6
                            FROM rem r
                            JOIN calidad.Skills s
                                ON s.Nombre COLLATE Latin1_General_CS_AS = r.Skill COLLATE Latin1_General_CS_AS -- Aquí se fuerza el Case-Sensitive
                               AND s.IsActive = 1
                            WHERE s.CampanaID = :campana_id
                            ORDER BY Nivel_1, Nivel_2
                        """)

                        result = connection.execute(query_arbol, {"campana_id": campana_id})
                    elif plataforma_nombre == 'AsterVoIP':
                        query_arbol = text("""
                            SELECT DISTINCT
                                CASE WHEN sf.Submotivo IS NOT NULL AND sf.Submotivo <> '' 
                                     THEN sf.Motivo + ' - ' + sf.Submotivo 
                                     ELSE sf.Motivo END AS Tipificacion,
                                sf.Motivo AS Nivel_1,
                                sf.Submotivo AS Nivel_2,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_3,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_4,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_5,
                                CAST(NULL AS VARCHAR(150)) AS Nivel_6
                            FROM [Acme].[Farmalux].[SalesForce_casos] sf
                            WHERE sf.[Última fecha/hora de modificación de caso] > DATEADD(Day, -30, GETDATE())
                              AND sf.Motivo IS NOT NULL AND sf.Motivo <> ''
                            ORDER BY Nivel_1, Nivel_2
                        """)
                        result = connection.execute(query_arbol)

                    rows = result.fetchall()

                    # Construcción del árbol recursivo
                    tree = []
                    
                    def find_or_create_node(current_level_list, name, full_path):
                        for node in current_level_list:
                            if node['label'] == name:
                                return node
                        new_node = {'label': name, 'value': full_path, 'children': []}
                        current_level_list.append(new_node)
                        return new_node

                    for row in rows:
                        # row[0] es la Tipificación completa
                        # row[1] a row[6] son los niveles
                        niveles = [n for n in row[1:] if n is not None and n != '']
                        
                        current_list = tree
                        path_accumulated = []
                        
                        for i, nivel in enumerate(niveles):
                            path_accumulated.append(nivel)
                            # Reconstruimos el path parcial para que el frontend pueda enviar "Ventas - Alta" si selecciona el nivel 2
                            # Nota: Asumimos ' - ' como separador estándar para el valor, o usamos el string original si es hoja
                            partial_path = " - ".join(path_accumulated)
                            
                            # Si es el último nivel de esta fila, y coincide con la tipificación completa, usamos la completa
                            # Esto ayuda si los separadores originales eran "->"
                            if i == len(niveles) - 1:
                                partial_path = row[0] # Usar el string exacto de la DB para la hoja final
                            
                            node = find_or_create_node(current_list, nivel, partial_path)
                            current_list = node['children']
                            
                    return tree

                if plataforma_nombre == 'CXOne':
                    # ... (Lógica existente de CXOne) ...
                    result = connection.execute(
                        text("""
                        SELECT Distinct
                            [callTagging]
                        FROM [Acme].[ALARMIX].[Grabaciones]
                        join calidad.Skills
                            on Grabaciones.skillName = Skills.[Nombre] and Skills.IsActive = 1
                        join calidad.Campanas
                            on Skills.CampanaID = Campanas.CampanaID and Campanas.IsActive = 1
                        WHERE callTagging is not null and callTagging <> ''
                        and campanas.CampanaID = :campana_id
                        order by callTagging
                        """),
                        {"campana_id": campana_id}
                    )
                    tipificaciones = [row[0] for row in result.fetchall()]
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")
        
        return tipificaciones

    #*Post
    def crear_campaña(self, nombre: str, Empresa_id: int, plataforma_id: int) -> int:
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""Exec calidad.sp_CrearCampana
                        @NombreCampana = :nombre,
                        @EmpresaID = :empresa_id,
                        @PlataformaID = :plataforma_id"""),
                    {
                        "nombre": nombre,
                        "empresa_id": Empresa_id,
                        "plataforma_id": plataforma_id
                    }
                )
                # A Cursor/Result may be returned even if no row was produced; fetch safely
                row = result.fetchone()
                if row is None:
                    raise ValueError("No se pudo crear la campaña.")
                return row[0]
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def crear_plantilla(self, nombre: str, descripcion: Optional[str], system_prompt: str, campanas_id:int, recordatorio: Optional[str] = None, modelo_ia: Optional[str] = None, nivel_razonamiento: Optional[str] = None) -> int:
        with self.engine.begin() as connection:
            try:
                result = connection.execute(
                    text("""Exec calidad.sp_CrearPlantillaBase
                        @NombrePlantilla = :nombre,
                        @Descripcion = :descripcion,
                        @SystemPrompt = :system_prompt,
                        @Recordatorio = :recordatorio,
                        @CampanaID = :CampanaID,
                        @ModeloIA = :modelo_ia,
                        @NivelRazonamiento = :nivel_razonamiento"""),
                    {
                        "nombre": nombre,
                        "descripcion": descripcion,
                        "system_prompt": system_prompt,
                        "recordatorio": recordatorio,
                        "CampanaID": campanas_id, # Debe ser una cadena separada por comas
                        "modelo_ia": modelo_ia,
                        "nivel_razonamiento": nivel_razonamiento,
                    }
                )
                # A Cursor/Result may be returned even if no row was produced; fetch safely
                row = result.fetchone()
                if row is None:
                    raise ValueError("No se pudo crear la plantilla.")
                return row[0]
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def _tiene_columna_es_opcional(self, connection) -> bool:
        """¿Está aplicada la migración 2026-08-05b (calidad.Atributos.EsOpcional)?

        Se chequea antes de escribir/copiar la columna en vez de intentarlo y atrapar el
        error: un statement fallido dentro de la transacción del alta/duplicado dejaría
        la operación a medias. Mismo criterio que la degradación de Ponderacion.
        """
        try:
            return connection.execute(
                text("""SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = 'calidad'
                          AND TABLE_NAME = 'Atributos'
                          AND COLUMN_NAME = 'EsOpcional'""")
            ).first() is not None
        except sqlalchemy_exc.SQLAlchemyError:
            return False

    def _nombre_copia_disponible(self, connection, nombre_original: str, campana_id: int) -> str:
        """Devuelve '<nombre> (copia)' o, si ya existe en esa campaña,
        '<nombre> (copia 2)', '(copia 3)', ... Evita listados con nombres
        repetidos cuando se duplica varias veces la misma plantilla."""
        result = connection.execute(
            text("""SELECT Nombre FROM calidad.Plantillas
                    WHERE CampanaID = :campana_id AND IsActive = 1"""),
            {"campana_id": campana_id},
        )
        existentes = {row[0].strip().lower() for row in result.fetchall() if row[0]}

        # El nombre base se limita a 255 - len(" (copia NN)") para no pasarse del
        # largo de la columna (nvarchar(255)).
        base = (nombre_original or "Plantilla")[:240].strip()
        candidato = f"{base} (copia)"
        intento = 2
        while candidato.strip().lower() in existentes:
            candidato = f"{base} (copia {intento})"
            intento += 1
        return candidato

    def duplicar_plantilla(self, plantilla_id: int, nuevo_nombre: Optional[str] = None,
                           campana_id: Optional[int] = None) -> Dict[str, Any]:
        """Crea una copia de una plantilla con todos sus atributos activos.

        Args:
            plantilla_id (int): ID de la plantilla a duplicar.
            nuevo_nombre (str): Nombre de la copia. Si no viene, se usa
                '<nombre original> (copia)' (con sufijo numérico si hace falta).
            campana_id (int): Campaña destino. Si no viene, la copia queda en la
                misma campaña que el original.
        Returns:
            Dict con 'plantilla_id', 'nombre' y 'campana_id' de la copia.
        """
        with self.engine.begin() as connection:
            try:
                # first() (y no fetchone()) en las lecturas de esta función: cierra el
                # cursor al leer la fila. Sin MARS, el driver ODBC rechaza la sentencia
                # siguiente si el result set anterior quedó abierto.
                origen = connection.execute(
                    text("""SELECT Nombre, descripcion, SystemPrompt, Recordatorio, CampanaID, ModeloIA,
                                   NivelRazonamiento
                            FROM calidad.Plantillas
                            WHERE PlantillaID = :plantilla_id AND IsActive = 1"""),
                    {"plantilla_id": plantilla_id},
                ).first()
                if origen is None:
                    raise ValueError(f"No se encontró la plantilla con id {plantilla_id}")

                campana_destino = int(campana_id) if campana_id else int(origen.CampanaID)
                nombre = (nuevo_nombre or "").strip() or self._nombre_copia_disponible(
                    connection, origen.Nombre, campana_destino
                )

                result = connection.execute(
                    text("""Exec calidad.sp_CrearPlantillaBase
                        @NombrePlantilla = :nombre,
                        @Descripcion = :descripcion,
                        @SystemPrompt = :system_prompt,
                        @Recordatorio = :recordatorio,
                        @CampanaID = :CampanaID,
                        @ModeloIA = :modelo_ia,
                        @NivelRazonamiento = :nivel_razonamiento"""),
                    {
                        "nombre": nombre,
                        "descripcion": origen.descripcion,
                        "system_prompt": origen.SystemPrompt,
                        "recordatorio": origen.Recordatorio,
                        "CampanaID": campana_destino,
                        # La copia hereda el modelo de la original aunque quien
                        # duplica no tenga 'template:modelo_ia': no se lo muestra,
                        # pero la copia tiene que auditar igual que el original.
                        "modelo_ia": origen.ModeloIA,
                        # Mismo criterio que el modelo: la copia tiene que auditar
                        # igual que el original aunque quien duplica no vea el campo.
                        "nivel_razonamiento": origen.NivelRazonamiento,
                    },
                )
                row = result.first()
                if row is None:
                    raise RuntimeError("No se pudo crear la copia de la plantilla.")
                nueva_plantilla_id = int(row[0])

                # Atributos: copia directa en un solo INSERT. No se usa
                # sp_AgregarAtributosAPlantilla porque ese SP no persiste
                # DarAviso/FrasesAviso y la copia debe ser fiel al original.
                # EsOpcional se copia solo si la migración 2026-08-05b está aplicada.
                columnas_extra = ", EsOpcional" if self._tiene_columna_es_opcional(connection) else ""
                connection.execute(
                    text(f"""INSERT INTO calidad.Atributos
                                (PlantillaID, NombreAtributo, PromptAdyacente, TipoDato,
                                 Restricciones, Orden, DarAviso, FrasesAviso, Ponderacion{columnas_extra})
                            SELECT :nueva_id, NombreAtributo, PromptAdyacente, TipoDato,
                                   Restricciones, Orden, DarAviso, FrasesAviso, Ponderacion{columnas_extra}
                            FROM calidad.Atributos
                            WHERE PlantillaID = :plantilla_id AND IsActive = 1"""),
                    {"nueva_id": nueva_plantilla_id, "plantilla_id": plantilla_id},
                )
                # La copia lee los mismos documentos de referencia que el original.
                conocimiento_plantilla.copiar_seleccion(connection, plantilla_id, nueva_plantilla_id)

                return {
                    "plantilla_id": nueva_plantilla_id,
                    "nombre": nombre,
                    "campana_id": campana_destino,
                }
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    # ----------------------------------------------------------------------- #
    # Gate de señales al guardar un atributo                                    #
    # ----------------------------------------------------------------------- #
    # POR QUÉ ACÁ Y NO EN EL ROUTER: por el mismo motivo que el freno de transcripción.
    # Los atributos entran por dos caminos —el editor y lo que aplica el asistente de IA—
    # y los dos pasan por estos métodos. Un chequeo puesto en el router cubriría uno solo.
    #
    # QUÉ FRENA: solo las señales de severidad ALTA, y solo las que ESTE guardado
    # introduce. Un atributo viejo que ya venía con un enum sin salida segura se puede
    # seguir editando (y reordenando) sin quedar preso de un problema que no creó quien
    # lo está tocando ahora; lo que no se puede es agregar uno nuevo. Es la misma regla
    # que ya usa _validar_texto_libre_si_cambio, por el mismo motivo.

    def _nombres_de_atributos(self, connection, plantilla_id: int,
                              excluir_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Los otros atributos activos de la plantilla (solo id y nombre).

        Alcanza con el nombre: es lo único que las señales miran de los hermanos (dos
        atributos homónimos colapsan en la misma clave del JSON de la IA).
        """
        try:
            filas = connection.execute(
                text("""SELECT AtributoID, NombreAtributo FROM calidad.Atributos
                        WHERE PlantillaID = :pid AND IsActive = 1"""),
                {"pid": plantilla_id},
            ).fetchall()
        except sqlalchemy_exc.SQLAlchemyError:
            return []  # Si no se pueden leer, se chequea el atributo solo.
        return [{"id": f[0], "nombre": f[1]} for f in filas
                if excluir_id is None or f[0] != excluir_id]

    def _senales_altas(self, atributo: Dict[str, Any],
                       hermanos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Import local: asistente_plantillas levanta el cliente de Gemini y no tiene
        # sentido arrastrarlo en cada import del manager (que entra en el arranque de
        # todo). Acá ya estamos en un guardado, el costo es irrelevante.
        from AuditorIA import asistente_plantillas

        canonico = asistente_plantillas.normalizar_atributos([atributo])[0]
        senales = asistente_plantillas.senales_de_atributo(canonico, hermanos=hermanos)
        return [s for s in senales if s["severidad"] == asistente_plantillas.SEVERIDAD_ALTA]

    def _gate_senales(self, connection, plantilla_id: Optional[int], entrante: Dict[str, Any],
                      previo: Optional[Dict[str, Any]] = None, excluir_id: Optional[int] = None,
                      forzar: bool = False, usuario: Optional[str] = None,
                      hermanos_extra: Optional[List[Dict[str, Any]]] = None) -> None:
        """Frena el guardado si el atributo INTRODUCE problemas de severidad alta.

        `previo` es el atributo tal como está guardado hoy (None en un alta): sus señales
        se descuentan para no cobrarle a nadie los problemas que ya estaban.
        `hermanos_extra` son atributos que todavía no están en la base pero van a quedar en
        la misma plantilla: los anteriores de esta misma tanda de alta. Sin ellos, dos
        atributos homónimos creados de una sola vez pasarían el gate.
        `forzar` guarda igual y lo deja logueado: el problema queda en la plantilla, así
        que igual va a aparecer en el semáforo y en el reporte semanal de salud.
        """
        if not senales_prompt.bloquea_altas() or plantilla_id is None:
            return

        hermanos = self._nombres_de_atributos(connection, plantilla_id, excluir_id=excluir_id)
        hermanos += list(hermanos_extra or [])
        altas = self._senales_altas(entrante, hermanos)
        if previo is not None:
            ya_estaban = {s["clave"] for s in self._senales_altas(previo, hermanos)}
            altas = [s for s in altas if s["clave"] not in ya_estaban]
        if not altas:
            return

        claves = ", ".join(s["clave"] for s in altas)
        if forzar:
            logger.warning(
                "Gate de plantillas salteado: usuario=%s plantilla=%s atributo=%r señales=[%s]",
                usuario or "?", plantilla_id, entrante.get("nombre"), claves,
            )
            return
        raise senales_prompt.SenalesAltasError(altas)

    def crear_atributos_plantilla(self, plantilla_id: int, atributos: List[Dict[str, Any]],
                                  forzar: bool = False, usuario: Optional[str] = None) -> None:
        """Crea atributos para una plantilla dada.
        Args:
            plantilla_id (int): ID de la plantilla.
        atributos (List[Dict[str, Any]]): Lista de diccionarios con los atributos donde los diccionarios deben tener las claves "nombre", "prompt", "tipo", "restricciones" y "orden".
    Example of atributos:
        [
            {
                "nombre": "Ejemplo Atributo 1",
                "prompt": "¿El agente saludó al cliente?",
                "tipo": "boolean",
                "restricciones": null,
                "orden": 1
            },
            {
                "nombre": "Ejemplo Atributo 2",
                "prompt": "Califica la calidad de la llamada del 1 al 5.",
                "tipo": "enum",
                "restricciones": {"values": [1, 2, 3, 4, 5]},
                "orden": 2
            }
    """
        
        if not atributos:
            return

        # Freno al "atributo transcripción" (ver AuditorIA/limites_texto.py). Se valida
        # acá y no en el router para que también cubra a las plantillas que propone el
        # asistente de IA, que entran por este mismo camino. Lanza ValueError -> 400.
        limites_texto.validar_atributos(atributos)

        # Convert the Python list of atributos to a JSON string using json.dumps to avoid pandas 'orient' typing issues
        Json_atributos = json.dumps(atributos, ensure_ascii=False)

        with self.engine.begin() as connection:
            # Gate de señales (ver _gate_senales). Se corre para TODOS los atributos antes
            # de escribir el primero: si uno está mal, no queda media tanda creada. Los
            # hermanos incluyen a los que vienen en esta misma tanda, así que un nombre
            # repetido dentro del alta también se detecta.
            entrantes_previos: List[Dict[str, Any]] = []
            for atributo in atributos:
                self._gate_senales(
                    connection, plantilla_id, atributo,
                    forzar=forzar, usuario=usuario, hermanos_extra=entrantes_previos,
                )
                entrantes_previos.append(atributo)

            try:
                # UNA sola llamada con la lista completa: el SP hace un
                # INSERT ... SELECT FROM OPENJSON(@AtributosJSON), o sea que ya inserta
                # todos los atributos del JSON de una. Llamarlo dentro de un for (una vez
                # por atributo, pasándole siempre la lista entera) insertaba N copias de
                # cada uno. No se notaba porque el editor crea los atributos de a uno.
                connection.execute(
                    text("""Exec calidad.sp_AgregarAtributosAPlantilla
                        @PlantillaID = :plantilla_id,
                        @AtributosJSON = :atributos_json"""),
                    {
                        "plantilla_id": plantilla_id,
                        "atributos_json": Json_atributos
                    }
                )

                # Campos que el SP de alta NO persiste (o que puede no conocer todavía):
                # se completan con un UPDATE por atributo, matcheando por nombre dentro de
                # la plantilla. Best-effort: si algo de esto falla no se pierde el alta.
                tiene_es_opcional = self._tiene_columna_es_opcional(connection)
                for atributo in atributos:
                    sets = []
                    params = {"pid": plantilla_id, "n": atributo.get("nombre")}

                    # Ponderación: el SP la inserta, pero se reescribe por si la BD
                    # todavía tiene la versión previa a EC/PONDERACION.
                    pond = atributo.get("ponderacion")
                    if pond is not None:
                        sets.append("Ponderacion = :p")
                        params["p"] = float(pond)

                    # DarAviso/FrasesAviso: el SP de alta directamente los ignora, así que
                    # sin esto la alerta configurada al crear el atributo se perdía y había
                    # que volver a entrar a editarlo para que quedara guardada.
                    if atributo.get("DarAviso") is not None:
                        sets.append("DarAviso = :av")
                        params["av"] = 1 if atributo.get("DarAviso") else 0
                    if atributo.get("FrasesAviso") is not None:
                        sets.append("FrasesAviso = :fr")
                        params["fr"] = atributo.get("FrasesAviso")

                    if atributo.get("es_opcional") and tiene_es_opcional:
                        sets.append("EsOpcional = 1")

                    if not sets:
                        continue
                    connection.execute(
                        text(f"""UPDATE calidad.Atributos SET {', '.join(sets)}
                                 WHERE PlantillaID = :pid AND NombreAtributo = :n AND IsActive = 1"""),
                        params,
                    )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def _atributo_guardado(self, connection, atributo_id: int) -> Optional[Dict[str, Any]]:
        """El atributo tal como está hoy en la base, en la misma forma que usa el editor.

        Es el "antes" contra el que se compara una edición: sin él no se puede distinguir
        un problema que alguien acaba de introducir de uno que la plantilla ya tenía.
        Devuelve None si no se puede leer (ahí el gate no corre y la edición sigue).
        """
        tiene_es_opcional = self._tiene_columna_es_opcional(connection)
        col_opcional = "EsOpcional" if tiene_es_opcional else "CAST(0 AS bit) AS EsOpcional"
        try:
            fila = connection.execute(
                text(f"""SELECT PlantillaID, NombreAtributo, PromptAdyacente, TipoDato,
                                Restricciones, Orden, DarAviso, FrasesAviso, Ponderacion,
                                {col_opcional}
                         FROM calidad.Atributos WHERE AtributoID = :id"""),
                {"id": atributo_id},
            ).fetchone()
        except sqlalchemy_exc.SQLAlchemyError:
            return None
        if not fila:
            return None
        return {
            "id": atributo_id,
            "plantilla_id": fila[0],
            "nombre": fila[1],
            "prompt": fila[2],
            "tipo": fila[3],
            "restricciones": fila[4],
            "orden": fila[5],
            "DarAviso": fila[6],
            "FrasesAviso": fila[7],
            "ponderacion": fila[8],
            "es_opcional": fila[9],
        }

    def _validar_texto_libre_si_cambio(self, connection, atributo_id: int, nombre: Optional[str],
                                       prompt: Optional[str], tipo: Optional[str]) -> None:
        """Valida el atributo editado contra los límites de texto libre, si hubo cambios.

        Lee el valor guardado y solo valida cuando el nombre o el prompt entrantes son
        distintos: así el freno aplica a lo que se escribe de ahora en más sin trabar la
        edición (ni el reordenamiento) de los atributos que ya estaban.
        """
        if nombre is None and prompt is None:
            return
        try:
            fila = connection.execute(
                text("""SELECT NombreAtributo, PromptAdyacente, TipoDato
                        FROM calidad.Atributos WHERE AtributoID = :id"""),
                {"id": atributo_id},
            ).fetchone()
        except sqlalchemy_exc.SQLAlchemyError:
            fila = None  # Si no se puede leer, no bloqueamos la edición.

        nombre_actual, prompt_actual, tipo_actual = (fila[0], fila[1], fila[2]) if fila else (None, None, None)
        cambio = (nombre is not None and (nombre or "").strip() != (nombre_actual or "").strip()) or \
                 (prompt is not None and (prompt or "").strip() != (prompt_actual or "").strip())
        if not cambio:
            return

        limites_texto.validar_atributo({
            "nombre": nombre if nombre is not None else nombre_actual,
            "prompt": prompt if prompt is not None else prompt_actual,
            "tipo": tipo if tipo is not None else tipo_actual,
        })

    def modificar_atributo(self, atributo_id: int, nombre: Optional[str]= None, prompt: Optional[str]= None, tipo: Optional[str]= None, restricciones: Optional[Dict[str, Any]] = None, orden: Optional[int] = None, DarAviso: Optional[bool] = None, FrasesAviso: Optional[str] = None, ponderacion: Optional[float] = None, es_opcional: Optional[bool] = None, forzar: bool = False, usuario: Optional[str] = None) -> None:
        """Modifica un atributo de una plantilla dada.
        Args:
            atributo_id (int): ID del atributo a modificar.
            nombre (str): Nuevo nombre del atributo.
            prompt (str): Nuevo prompt del atributo.
            tipo (str): Nuevo tipo del atributo.
            restricciones (Union[Dict[str, Any], None]): Nuevas restricciones del atributo.
            orden (int): Nuevo orden del atributo.
            DarAviso (bool): Indica si se debe alertar.
            FrasesAviso (str): Palabras clave separadas por comas.
            ponderacion (float): Peso relativo para el puntaje ponderado (0 = no participa).
            es_opcional (bool): Si True, la IA puede dejar el atributo sin responder
                cuando la interacción no da evidencia. None = no se toca.
        """

        # Convertir las restricciones a JSON si no es None
        restricciones_json = json.dumps(restricciones, ensure_ascii=False) if restricciones is not None else None

        with self.engine.begin() as connection:
            # Freno al "atributo transcripción" (ver AuditorIA/limites_texto.py). Solo si
            # el texto CAMBIA: este mismo PUT es el que usa el reordenamiento por drag&drop,
            # que reenvía el prompt tal cual está guardado. Si validáramos siempre, una
            # plantilla vieja escrita así quedaría imposible de reordenar.
            self._validar_texto_libre_si_cambio(connection, atributo_id, nombre, prompt, tipo)

            # Gate de señales (ver _gate_senales): frena solo lo que ESTA edición
            # introduce. El drag&drop de reordenamiento entra por acá reenviando los
            # valores guardados, así que sin la comparación contra el estado previo una
            # plantilla vieja con problemas quedaría imposible de reordenar.
            previo = self._atributo_guardado(connection, atributo_id)
            if previo is not None:
                entrante = dict(previo)
                for campo, valor in (("nombre", nombre), ("prompt", prompt), ("tipo", tipo),
                                     ("orden", orden), ("DarAviso", DarAviso),
                                     ("FrasesAviso", FrasesAviso), ("ponderacion", ponderacion),
                                     ("es_opcional", es_opcional)):
                    if valor is not None:
                        entrante[campo] = valor
                if restricciones is not None:
                    entrante["restricciones"] = restricciones
                self._gate_senales(
                    connection, previo.get("plantilla_id"), entrante, previo=previo,
                    excluir_id=atributo_id, forzar=forzar, usuario=usuario,
                )
            try:
                connection.execute(
                    text("""Exec calidad.sp_ModificarAtributo
                        @AtributoID = :atributo_id,
                        @NombreAtributo = :nombre,
                        @PromptAdyacente = :prompt,
                        @TipoDato = :tipo,
                        @Restricciones = :restricciones,
                        @Orden = :orden,
                        @DarAviso = :DarAviso,
                        @FrasesAviso = :FrasesAviso"""),
                    {
                        "atributo_id": atributo_id,
                        "nombre": nombre,
                        "prompt": prompt,
                        "tipo": tipo,
                        "restricciones": restricciones_json,
                        "orden": orden,
                        "DarAviso": DarAviso,         # <-- NUEVO
                        "FrasesAviso": FrasesAviso    # <-- NUEVO
                    }
                )
                # Ponderación: UPDATE directo (no depende de que el SP exponga @Ponderacion).
                if ponderacion is not None:
                    connection.execute(
                        text("UPDATE calidad.Atributos SET Ponderacion = :p WHERE AtributoID = :id"),
                        {"p": float(ponderacion), "id": atributo_id},
                    )
                # Ídem la marca de opcional (migración 2026-08-05b).
                if es_opcional is not None and self._tiene_columna_es_opcional(connection):
                    connection.execute(
                        text("UPDATE calidad.Atributos SET EsOpcional = :o WHERE AtributoID = :id"),
                        {"o": 1 if es_opcional else 0, "id": atributo_id},
                    )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def modificar_plantilla(self, plantilla_id: int, nombre: Optional[str]= None, descripcion: Optional[str]= None, system_prompt: Optional[str]= None, recordatorio: Optional[str] = None, modelo_ia: Optional[str] = None, nivel_razonamiento: Optional[str] = None) -> None:
        """Modifica una plantilla dada.
        Args:
            plantilla_id (int): ID de la plantilla a modificar.
        nombre (str): Nuevo nombre de la plantilla.
        descripcion (str): Nueva descripción de la plantilla.
        system_prompt (str): Nuevo system prompt de la plantilla.
        recordatorio (Union[str, None]): Nuevo recordatorio de la plantilla.
        modelo_ia (Union[str, None]): Nuevo modelo de Gemini de la plantilla.
        nivel_razonamiento (Union[str, None]): Nuevo nivel de razonamiento
            (LOW/MEDIUM/HIGH, ver AuditorIA/razonamiento.py). None = no se toca.
    """

        with self.engine.begin() as connection:
            try:
                connection.execute(
                    text("""Exec calidad.sp_ModificarPlantilla
                        @PlantillaID = :plantilla_id,
                        @NombrePlantilla = :nombre,
                        @Descripcion = :descripcion,
                        @SystemPrompt = :system_prompt,
                        @Recordatorio = :recordatorio,
                        @ModeloIA = :modelo_ia,
                        @NivelRazonamiento = :nivel_razonamiento"""),
                    {
                        "plantilla_id": plantilla_id,
                        "nombre": nombre,
                        "descripcion": descripcion,
                        "system_prompt": system_prompt,
                        "recordatorio": recordatorio,
                        "modelo_ia": modelo_ia,
                        "nivel_razonamiento": nivel_razonamiento,
                    }
                )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def desactivar_campana(self, campana_id: int) -> None:
        """Elimina una campana dada.
        Args:
            campana_id (int): ID de la campana a eliminar.
        """
        with self.engine.begin() as connection:
            try:
                connection.execute(
                    text("""Exec calidad.sp_DesactivarCampana
                        @TargetCampanaID = :campana_id"""),
                    {
                        "campana_id": campana_id
                    }
                )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def desactivar_plantilla(self, plantilla_id: int) -> None:
        """Elimina una plantilla dada.
        Args:
            plantilla_id (int): ID de la plantilla a eliminar.
    """

        with self.engine.begin() as connection:
            try:
                connection.execute(
                    text("""Exec calidad.sp_DesactivarPlantilla
                        @TargetPlantillaID = :plantilla_id"""),
                    {
                        "plantilla_id": plantilla_id
                    }
                )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def desactivar_atributo(self, atributo_id: int) -> None:
        """Elimina un atributo dado.
        Args:
            atributo_id (int): ID del atributo a eliminar.
    """

        with self.engine.begin() as connection:
            try:
                connection.execute(
                    text("""Exec calidad.sp_DesactivarAtributo
                        @TargetAtributoID = :atributo_id"""),
                    {
                        "atributo_id": atributo_id
                    }
                )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def desactivar_skill(self, campana_id: int, skills: List[str]) -> None:
        """Elimina una skill de una plantilla dada.
        Args:
            campana_id (int): ID de la campana.
            skill (List[str]): Nombre de la skill a eliminar.
    """

        with self.engine.begin() as connection:
            try:
                for skill in skills:
                    connection.execute(
                        text("""Exec calidad.sp_DesactivarSkill
                            @TargetCampanaID = :campana_id,
                            @NombreSkill = :skill"""),
                        {
                            "campana_id": campana_id,
                            "skill": skill
                        }
                    )
            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")

    def asignar_skill(self, campana_id: int, skills: List[str]) -> None:
        """Asigna una skill a una plantilla dada.
        Args:
            plantilla_id (int): ID de la plantilla.
        skill (List[str]): Nombre de la skill a asignar.
    """

        with self.engine.begin() as connection:
            try:
                if not skills:
                    print("No hay skills para asignar.")
                    return

                for skill in skills:
                    connection.execute(
                        text("""Exec calidad.sp_AsignarSkill
                                @TargetCampanaID = :campana_id,
                                @NombreSkill = :skill"""),
                        {
                            "campana_id": campana_id,
                            "skill": skill
                        }
                    )
                

            except sqlalchemy_exc.SQLAlchemyError as e:
                raise RuntimeError(f"Error al consultar la base de datos: {e}")


def main():
    from pydantic_settings import BaseSettings
    class Settings(BaseSettings):
        connection_string: str
        secret_key: str
        algorithm: str
        access_token_expire_minutes: int
        DEFAULT_LOG_DIR: str

        class Config:
            env_file = '.env'
            env_file_encoding = 'utf-8'
            extra = 'ignore'
    Settings = Settings() # type: ignore

    engine = create_engine(Settings.connection_string)
    plantillas_manager_instance = plantillas_manager(engine)
    plantilla = plantillas_manager_instance.obtener_plantilla(2)

    print(plantilla)

    print('Hello')

if __name__ == "__main__":
    main()