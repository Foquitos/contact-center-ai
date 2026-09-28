"""Acceso a datos del planificador: configuración, series históricas y corridas.

Todo lo que toca la base vive acá. La lógica (línea de base, detección de
atípicos, dimensionamiento) está en `app/planificador.py` y no sabe de SQL, así
que se puede probar sin conexión.

DE DÓNDE SALE LA SERIE HISTÓRICA
---------------------------------
De una tabla distinta por campaña, porque cada cliente manda su reporte en su
propio formato. El mapeo vive en `FUENTES`, y agregar una campaña nueva es
agregar una entrada ahí, no tocar el resto del módulo.

ATENCIÓN — LO QUE HAY EN ESAS TABLAS ES NUESTRA PORCIÓN, NO LA DEMANDA DEL CLIENTE
-----------------------------------------------------------------------------------
`dbo.[Voltara Enerval informe skills]` contiene únicamente las llamadas de BPO-04, o
sea las nuestras: verificado mes a mes contra `dbo.[Voltara informe IVR]`, coincide
con diferencias de 0 a 1 llamada. Voltara reparte su volumen entre varios contact
centers y nuestra porción va a pasar de ~40% a hasta 70%.

O sea que la serie histórica es `demanda_de_Voltara x nuestro_share`, con las dos
cosas mezcladas y sin forma de separarlas. Mientras siga así:

  - el pronóstico es de LO QUE VAMOS A RECIBIR, no de lo que va a tener Voltara;
  - `planificacion.Asignacion` queda como está y NO se aplica (aplicarla sobre una
    serie que ya tiene el share adentro lo contaría dos veces);
  - cuando cambie el reparto, el pronóstico va a estar mal por construcción.

`hay_demanda_total()` es el interruptor: cuando esté cargada la descarga del 100%
de las llamadas pasa a True, el pronóstico se hace sobre la demanda total y la
asignación se aplica aparte. Hasta entonces, la pantalla lo avisa.
"""

from __future__ import annotations

import json
import math
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from app import planificador as pl
from app import planificador_antiguedad as pant
from app import planificador_presencia as ppresencia

logger = logging.getLogger(__name__)

CAMPANA_VOLTARA = 20
# Hidra Técnico (HidraIN en calidad.Campanas, empresa HIDRA). Hidra Comercial es la 24,
# atiende por Hermes con otra gente y todavía no tiene fuente.
CAMPANA_HIDRA_TECNICO = 1
# Gasur (distribución de garrafas, Uruguay). La da de alta en calidad.Campanas
# la migración 2026-09-24d con este id fijo: el código lo necesita de antemano.
CAMPANA_GASUR = 30


class MigracionPendiente(RuntimeError):
    """El schema `planificacion` todavía no existe en la base."""


# --------------------------------------------------------------------- fuentes

class FuenteVoltara:
    """Reporte por intervalo y skill de Voltara (media hora, desde 2025-04-26)."""

    # El TMO se pondera por llamadas respondidas: el promedio simple de los
    # intervalos le da el mismo peso a uno de 3 llamadas que a uno de 300.
    SERIE = """
        SELECT s.Intervalo                                          AS momento,
               s.[Skill ID]                                         AS skill_id,
               SUM(CAST(s.[Volumen de llamadas entrantes]  AS int)) AS llamadas,
               SUM(CAST(s.[Volumen de llamadas respondidas] AS int)) AS atendidas,
               SUM(CAST(s.TMO AS float) * CAST(s.[Volumen de llamadas respondidas] AS float)) AS tmo_x_llamadas
        FROM [dbo].[Voltara Enerval informe skills] s
        WHERE s.Intervalo >= :desde AND s.Intervalo < :hasta
        GROUP BY s.Intervalo, s.[Skill ID]
        ORDER BY s.Intervalo
    """

    # Paciencia por skill: MLE exponencial con censura. Los que fueron atendidos
    # no dicen cuánto habrían aguantado, solo que aguantaron AL MENOS lo que
    # esperaron; por eso el denominador es la espera de TODAS las llamadas y no
    # solo la de las que cortaron.
    PACIENCIA = """
        SELECT i.Skill                                          AS skill,
               SUM(CASE WHEN i.[Nombre de Agente] IS NULL THEN 1 ELSE 0 END) AS abandonos,
               SUM(CAST(i.[Duración Cola] AS bigint))           AS espera_total
        FROM [dbo].[Voltara informe IVR] i
        WHERE i.BPO = 'BPO-04' AND i.Cola IS NOT NULL
          AND i.[Fecha de Inicio] >= :desde AND i.[Fecha de Inicio] < :hasta
        GROUP BY i.Skill
    """

    # Curva de paciencia: para cada tramo de espera, cuántos cortaron y cuántos
    # fueron atendidos. Con eso se estima Kaplan-Meier sin suponer ninguna forma.
    # El tramo tope (>= TOPE_ESPERA_SEG) se agrupa porque ahí ya no se está
    # midiendo paciencia sino el corte del sistema: en Emergencias hay 207
    # abandonos contra 35 atenciones justo en los 600s.
    PACIENCIA_CURVA = """
        SELECT Skill AS skill, bucket,
               SUM(CASE WHEN abandonada = 1 THEN 1 ELSE 0 END) AS abandonos,
               SUM(CASE WHEN abandonada = 0 THEN 1 ELSE 0 END) AS atendidas
        FROM (
            SELECT i.Skill,
                   CASE WHEN i.[Duración Cola] >= :tope THEN :tope
                        ELSE (i.[Duración Cola] / :paso) * :paso END AS bucket,
                   CASE WHEN i.[Nombre de Agente] IS NULL THEN 1 ELSE 0 END AS abandonada
            FROM [dbo].[Voltara informe IVR] i
            WHERE i.BPO = 'BPO-04' AND i.Cola IS NOT NULL AND i.Skill IS NOT NULL
              AND i.[Duración Cola] >= 0
              AND i.[Fecha de Inicio] >= :desde AND i.[Fecha de Inicio] < :hasta
        ) t
        GROUP BY Skill, bucket
        ORDER BY Skill, bucket
    """

    # Para calibrar la disponibilidad: el NDS real a 20s del pool y cuánta gente
    # había. `Contestadas Umbral` NO sirve (está calculada a 30s), así que el
    # numerador se arma sumando los buckets hasta 20s.
    # Operadores conectados por intervalo, de la MISMA vista que usa el tablero
    # de la operación. Reproduce sus números exactos: el 2026-09-09 a las 11:00
    # da 62,13 y el tablero muestra 62.
    #
    # POR QUÉ LA VISTA Y NO EL INFORME DE SKILLS. Ahí `[Agentes Logueados]` es
    # "logueados que tienen este skill" y los operadores son multiskill: sumarlo
    # entre colas cuenta a la misma persona una vez por cada una (283 contra 62
    # reales en esa misma media hora). La vista deduplica por `nomina_id` sobre
    # el informe por AGENTE y devuelve operadores-equivalentes
    # (tiempo logueado / 1800), que además es lo correcto para un intervalo donde
    # alguien entró a la mitad.
    #
    # Tampoco sale de `TMO_Voltara`: ahí sólo figura el que atendió al menos una
    # llamada, así que el conectado sin llamadas no se cuenta (56 contra 62).
    #
    # Es de la campaña entera y no por skill —la vista no discrimina cola—, lo
    # cual alcanza mientras Voltara tenga un solo pool. Con dos pools habría que
    # abrirla.
    CONECTADOS = """
        SELECT [Fecha/Horas] AS momento, Cant_op AS agentes
        FROM dbo.Tablero_Agentes_Voltara
        WHERE [Fecha/Horas] >= :desde AND [Fecha/Horas] < :hasta
    """

    # Los mismos conectados pero partidos por la sub-campaña de RRHH de cada
    # persona, para saber cuántos eran del pool, cuántos del refuerzo (Digital) y
    # cuántos de otras. Suma EXACTAMENTE lo mismo que `CONECTADOS`: mismo informe,
    # mismo cruce por usuario y la misma deduplicación por persona e intervalo
    # (el informe trae una fila por skill, y sumar sin el MAX triplica).
    # Verificado el 2026-09-09 de 9 a 12:30: 44,34 / 48,15 / ... / 62,13 igual
    # que la vista en las ocho medias horas.
    #
    # LA SUB-CAMPAÑA ES LA DE ESE DÍA, no la actual: sale de la fila de payroll que
    # tenía la persona ese día (o el anterior, para el turno que cruza la
    # medianoche). Con la actual, Digital daba 431 h en 25 días contra 657 h con la
    # de ese día, porque en la ventana hubo gente que cambió de sub-campaña.
    # `campana_id` NULL = conectado sin fila de payroll ese día.
    #
    # `agentes_en_linea` es lo mismo SIN LAS PAUSAS: logueado menos «en pausa»
    # (break, pausa activa, administrativa, técnica, personal, capacitación, de
    # login y sin razón). Es el que se compara contra «hacía falta en línea»: el
    # que está en su break está logueado pero no atiende. El logueo y la pausa son
    # del agente —se repiten igual en cada fila de operación—, así que el MAX por
    # persona de la resta es la resta de la persona. Medido del 1 al 15/09: 13.076
    # logueados, 11.532 en línea; la pausa es 11,8% (break 8,5%, activa 2,1%).
    CONECTADOS_POR_CAMPANA = """
        WITH ag AS (
            SELECT a.Intervalo, u.nomina_id,
                   MAX(CAST(a.[Tiempo Agentes Logueados] AS float)) AS seg,
                   MAX(CASE WHEN a.[Tiempo Agentes Logueados] > ISNULL(a.[Tiempo Agentes en pausa], 0)
                            THEN CAST(a.[Tiempo Agentes Logueados] AS float)
                               - CAST(ISNULL(a.[Tiempo Agentes en pausa], 0) AS float)
                            ELSE 0 END) AS seg_linea
            FROM dbo.[Voltara Enerval informe agente] a
            JOIN dbo.usuarios u
              ON a.Login COLLATE SQL_Latin1_General_CP1_CS_AS
               = u.usuario COLLATE SQL_Latin1_General_CP1_CS_AS
            WHERE a.Intervalo >= :desde AND a.Intervalo < :hasta
            GROUP BY a.Intervalo, u.nomina_id
        )
        SELECT ag.Intervalo AS momento, x.campana_id,
               SUM(ag.seg) / 1800.0 AS agentes,
               SUM(ag.seg_linea) / 1800.0 AS agentes_en_linea
        FROM ag
        OUTER APPLY (
            SELECT TOP 1 o.campana_id
            FROM dbo.payroll p
            JOIN dbo.operadores o ON o.id = p.id_operadores
            JOIN dbo.nomina n ON n.id = o.legajo_id
            WHERE n.documento = ag.nomina_id
              AND p.fecha IN (CAST(ag.Intervalo AS date),
                              DATEADD(day, -1, CAST(ag.Intervalo AS date)))
            ORDER BY CASE WHEN ag.Intervalo >= p.inicio AND ag.Intervalo < p.final
                          THEN 0 ELSE 1 END,
                     p.fecha DESC, p.horas_programadas DESC
        ) x
        GROUP BY ag.Intervalo, x.campana_id
    """

    # Gente de las sub-campañas que atienden la línea A VECES (PoolSubCampana,
    # clase 'telefonica_parcial': Contingencia, Gestión SVP, BU - Anfitrión) que
    # tenía turno en el intervalo Y estuvo logueada en la línea en ese intervalo.
    # Es lo que suman a «tenían turno»: los días que no atienden están en digital
    # o en la sucursal, y contar ese turno entero lo haría pasar por ausentismo.
    # Mismo registro, mismas clases de código y mismo filtro de puesto que
    # `_PAYROLL_TURNOS_REAL`; el primer intervalo del turno se redondea hacia abajo
    # como en `_contar_turnos_de`. El día anterior entra por los turnos que cruzan
    # la medianoche.
    TURNOS_PARCIALES_EN_LINEA = """
        WITH t AS (
            SELECT DISTINCT n.documento, p.inicio, p.final,
                   DATEADD(minute,
                           (DATEDIFF(minute, CAST(CAST(p.inicio AS date) AS datetime),
                                     p.inicio) / 30) * 30,
                           CAST(CAST(p.inicio AS date) AS datetime)) AS desde30
            FROM dbo.payroll p
            JOIN dbo.operadores o ON o.id = p.id_operadores
            JOIN dbo.nomina n ON n.id = o.legajo_id
            LEFT JOIN planificacion.CodigoPayroll c ON c.Codigo = p.codigo
            WHERE ISNULL(c.Clase, 'piso') IN ('piso', 'ausente')
              AND p.fecha >= DATEADD(day, -1, :desde) AND p.fecha < :hasta
              AND p.horas_programadas > 0
              AND o.campana_id IN :campanas
              AND p.inicio IS NOT NULL AND p.final IS NOT NULL
              AND p.final > p.inicio{puesto}
        ), l AS (
            SELECT DISTINCT a.Intervalo, u.nomina_id
            FROM dbo.[Voltara Enerval informe agente] a
            JOIN dbo.usuarios u
              ON a.Login COLLATE SQL_Latin1_General_CP1_CS_AS
               = u.usuario COLLATE SQL_Latin1_General_CP1_CS_AS
            WHERE a.Intervalo >= :desde AND a.Intervalo < :hasta
              AND a.[Tiempo Agentes Logueados] > 0
        )
        SELECT l.Intervalo AS momento, COUNT(DISTINCT l.nomina_id) AS personas
        FROM l
        JOIN t ON t.documento = l.nomina_id
              AND l.Intervalo >= t.desde30 AND l.Intervalo < t.final
        GROUP BY l.Intervalo
    """

    # Rinde de la gente nueva: segundos por llamada, por cola y por días desde el
    # pase a piso. Sale del detalle de llamadas (TMO_Voltara), que es el único con
    # la cola de cada llamada y quién la atendió; el informe por agente no trae la
    # cola, y sin ella el aprendizaje se mezcla con la mezcla de colas.
    #
    # EL DETALLE IDENTIFICA AL AGENTE POR NOMBRE, no por usuario. Se cruza contra
    # nombre + apellido de la nómina sin tildes ni mayúsculas, y SÓLO con los
    # nombres sin homónimos: uno le pasaría el aprendizaje de otro. Medido del
    # 17/08 al 13/09 cruza el 77% de las llamadas y ningún homónimo.
    #
    # Los días se topean en :antiguo para que la agrupación sea chica; el tramo lo
    # arma `planificador_antiguedad.tramo_de`, que es el único lugar donde viven.
    RINDE_POR_ANTIGUEDAD = """
        WITH gente AS (
            SELECT UPPER(LTRIM(RTRIM(nombre)) + ' ' + LTRIM(RTRIM(apellido)))
                       COLLATE Latin1_General_CI_AI AS nombre_completo,
                   MAX(fecha_piso) AS fecha_piso
            FROM dbo.nomina
            GROUP BY UPPER(LTRIM(RTRIM(nombre)) + ' ' + LTRIM(RTRIM(apellido)))
                         COLLATE Latin1_General_CI_AI
            HAVING COUNT(*) = 1
        ), llamadas AS (
            SELECT t.[Skill ID] AS skill_id, t.[Tiempo Total] AS segundos,
                   DATEDIFF(day, g.fecha_piso, CAST(t.[Fecha de Inicio] AS date)) AS dias
            FROM dbo.TMO_Voltara t
            JOIN gente g
              ON g.nombre_completo = UPPER(t.[Nombre de Agente]) COLLATE Latin1_General_CI_AI
            WHERE t.[Fecha de Inicio] >= :desde AND t.[Fecha de Inicio] < :hasta
              AND t.[Tiempo Total] > 0 AND t.[Skill ID] IS NOT NULL
        )
        SELECT skill_id, dias, COUNT(*) AS llamadas,
               SUM(CAST(segundos AS bigint)) AS segundos
        FROM (
            SELECT skill_id, segundos,
                   CASE WHEN dias < 0 THEN 0 WHEN dias > :antiguo THEN :antiguo
                        ELSE dias END AS dias
            FROM llamadas
        ) x
        GROUP BY skill_id, dias
    """

    # Cuántas llamadas atendió cada antigüedad en la historia con la que se midió
    # el TMO del pronóstico: es la mezcla contra la que se normaliza el peso de la
    # gente nueva. Del informe por agente, que cruza por usuario y cubre a todos.
    LLAMADAS_POR_ANTIGUEDAD = """
        WITH d AS (
            SELECT CAST(a.Intervalo AS date) AS fecha, u.nomina_id,
                   SUM(CAST(a.[volumen de llamadas respondidas] AS float)) AS llamadas
            FROM dbo.[Voltara Enerval informe agente] a
            JOIN dbo.usuarios u
              ON a.Login COLLATE SQL_Latin1_General_CP1_CS_AS
               = u.usuario COLLATE SQL_Latin1_General_CP1_CS_AS
            WHERE a.Intervalo >= :desde AND a.Intervalo < :hasta
              AND a.[Operación] = 1
            GROUP BY CAST(a.Intervalo AS date), u.nomina_id
        ), con_piso AS (
            SELECT d.llamadas,
                   DATEDIFF(day, (SELECT MAX(n.fecha_piso) FROM dbo.nomina n
                                  WHERE n.documento = d.nomina_id), d.fecha) AS dias
            FROM d
        )
        SELECT dias, SUM(llamadas) AS llamadas
        FROM (
            SELECT llamadas,
                   CASE WHEN dias < 0 THEN 0 WHEN dias > :antiguo THEN :antiguo
                        ELSE dias END AS dias
            FROM con_piso
        ) x
        GROUP BY dias
    """

    CALIBRACION = """
        WITH pool AS (
            SELECT s.Intervalo,
                   SUM(CAST(s.[Volumen de llamadas entrantes]   AS int)) AS entrantes,
                   SUM(CAST(s.[Volumen de llamadas respondidas] AS int)) AS atendidas,
                   SUM(CAST(s.[Contestadas  <= 1]     AS int)
                     + CAST(s.[Contestadas > 1 <= 5]  AS int)
                     + CAST(s.[Contestadas > 5 <= 10] AS int)
                     + CAST(s.[Contestadas > 10 <= 20] AS int))          AS hasta_20s,
                   SUM(CAST(s.TMO AS float) * CAST(s.[Volumen de llamadas respondidas] AS float)) AS tmo_x_llamadas
            FROM [dbo].[Voltara Enerval informe skills] s
            WHERE s.Intervalo >= :desde AND s.Intervalo < :hasta
              AND s.[Skill ID] IN :skills
            GROUP BY s.Intervalo
        ), presentes AS (
            -- Los presentes salen de la vista del tablero y NO de `TMO_Voltara`.
            -- Ahí sólo figura el que ATENDIÓ al menos una llamada en el
            -- intervalo, así que el que estuvo conectado y no recibió ninguna no
            -- se contaba: el 2026-09-09 a las 11:00 daba 56 contra 62 logueados.
            -- Y como este número es el DENOMINADOR de la disponibilidad
            -- (implícito / presentes), quedarse corto la dejaba optimista.
            SELECT [Fecha/Horas] AS Intervalo, Cant_op AS agentes
            FROM dbo.Tablero_Agentes_Voltara
            WHERE [Fecha/Horas] >= :desde AND [Fecha/Horas] < :hasta
        )
        SELECT p.Intervalo AS momento, p.entrantes, p.atendidas, p.hasta_20s,
               p.tmo_x_llamadas, a.agentes
        FROM pool p
        JOIN presentes a ON a.Intervalo = p.Intervalo
        WHERE p.atendidas > 0 AND a.agentes > 0
        ORDER BY p.Intervalo
    """


class FuenteHidraTecnico:
    """Hidra Técnico: la campaña HIDRAIN de Mitrol (media hora, desde 2023-07).

    Hidra son dos campañas que funcionan como dos empresas: Técnico (reclamos
    técnicos) atiende por Mitrol y Comercial por Hermes, con operadores distintos
    que no se pasan de una a otra. Ésta es sólo la de Mitrol.

    UNA SOLA COLA. En Mitrol la campaña es la cola: HIDRAIN es todo el Técnico, así
    que hay un skill y un pool. El `skill_id` es el `idCampania` de Mitrol (481),
    que es estable aunque le cambien el nombre. HIDRA001 (el IVR, ~2,3 M
    ingresadas, 12 atendidas en tres años) y HIDRA004 (~50 llamadas por semana sin
    agente) no son demanda de operadores.

    DE DÓNDE SALE CADA COSA (verificado 14 al 20/09/2026, cierran entre sí):
      - Volumen, TMO y conectados: `Acumuladores_de_campana`, por media hora y
        campaña, carga intradía e indexada por Intervalo. Las 12.328 ingresadas de
        la semana son exactamente las entrantes del detalle, y su `Login` es la
        suma del informe por agente (189,6 h el 14/09 en las dos).
      - Espera de cada llamada (paciencia y abandono): el detalle por campaña
        (`detalle_de_interacciones_por_campana_lote`), que es el único que trae las
        abandonadas en cola con su espera. Carga de noche y no tiene índice por
        fecha: sólo lo leen la calibración y la curva de paciencia.
      - Atendidas en 20 s: el detalle por agente, que carga intradía y está
        indexado por `Inicio`. Sólo tiene las que llegaron a un agente, que son las
        únicas que cuentan en el numerador.
      - Conectados por persona: `acumuladores_de_agentes_por_skill` (carga el día
        cerrado). Los operadores de Hidra Canal Telefónico (sub-campaña 57) sólo se
        loguean en HIDRAIN: medido la semana del 14/09, 1.071 h de 1.071.

    ES NUESTRA PORCIÓN. No hay descarga de la demanda total del cliente como la de
    Voltara, así que se pronostica lo que nos llega y `planificacion.Asignacion` no
    se aplica (ver el encabezado del módulo). Ojo con la historia: en septiembre de
    2024 el volumen cayó de ~100.000 a ~40.000 por mes y en mayo de 2026 volvió a
    subir a ~55.000. La ventana de 52 semanas y la corrección de nivel lo absorben,
    pero un modelo que mire más de un año hacia atrás (el clima mira 1.100 días)
    ve dos regímenes.
    """

    # La media hora de Mitrol. AHT es (Connect + Hold + ACW) / atendidas: el 14/09
    # da 180,7 s por la suma y 181,6 s ponderando la columna.
    SERIE = """
        SELECT a.Intervalo                                             AS momento,
               a.idCampania                                            AS skill_id,
               SUM(CAST(a.Ingresadas AS int))                          AS llamadas,
               SUM(CAST(a.AgentesAtendidas AS int))                    AS atendidas,
               SUM(CAST(a.AHT AS float) * CAST(a.AgentesAtendidas AS float)) AS tmo_x_llamadas
        FROM dbo.Acumuladores_de_campana a
        WHERE a.idCampania = 481
          AND a.Intervalo >= :desde AND a.Intervalo < :hasta
        GROUP BY a.Intervalo, a.idCampania
        ORDER BY a.Intervalo
    """

    # Una fila por llamada entrante (Entrante = 1: el detalle trae también las
    # filas de segmentos que no son la entrada a la cola). `skill` va con el nombre
    # de planificacion.Skill, que es por lo que se cruza la paciencia.
    PACIENCIA = """
        SELECT 'HIDRAIN'                                             AS skill,
               SUM(CASE WHEN d.Abandonada = 1 THEN 1 ELSE 0 END)    AS abandonos,
               SUM(CAST(d.EnCola AS bigint))                        AS espera_total
        FROM dbo.detalle_de_interacciones_por_campana_lote d
        WHERE d.idCampania = 481 AND d.Entrante = 1 AND d.EnCola >= 0
          AND d.fecha_inicio >= :desde AND d.fecha_inicio < :hasta
    """

    PACIENCIA_CURVA = """
        SELECT 'HIDRAIN' AS skill, bucket,
               SUM(CASE WHEN abandonada = 1 THEN 1 ELSE 0 END) AS abandonos,
               SUM(CASE WHEN abandonada = 0 THEN 1 ELSE 0 END) AS atendidas
        FROM (
            SELECT CASE WHEN d.EnCola >= :tope THEN :tope
                        ELSE (d.EnCola / :paso) * :paso END AS bucket,
                   CASE WHEN d.Abandonada = 1 THEN 1 ELSE 0 END AS abandonada
            FROM dbo.detalle_de_interacciones_por_campana_lote d
            WHERE d.idCampania = 481 AND d.Entrante = 1 AND d.EnCola >= 0
              AND d.fecha_inicio >= :desde AND d.fecha_inicio < :hasta
        ) t
        GROUP BY bucket
        ORDER BY bucket
    """

    # Operadores-equivalentes conectados (tiempo logueado / 1800), de la misma
    # tabla que el volumen: carga intradía, a diferencia del informe por agente,
    # que llega con el día cerrado.
    CONECTADOS = """
        SELECT a.Intervalo AS momento, SUM(CAST(a.Login AS float)) / 1800.0 AS agentes
        FROM dbo.Acumuladores_de_campana a
        WHERE a.idCampania = 481
          AND a.Intervalo >= :desde AND a.Intervalo < :hasta
        GROUP BY a.Intervalo
    """

    # Los conectados partidos por la sub-campaña de RRHH de ESE día, como en
    # Voltara (ver `FuenteVoltara.CONECTADOS_POR_CAMPANA`). `agentes_en_linea` es
    # `TIEMPO REAL DE LOGUEO`, que Mitrol ya da sin auxiliares ni Not Ready.
    #
    # El usuario se deduplica antes del cruce: `dbo.usuarios` tiene 1.435 logins
    # reciclados entre personas (ninguno de HIDRAIN entre junio y septiembre de
    # 2026) y un cruce directo contaría dos veces a quien tenga uno. El login que
    # no está en usuarios queda solo, con `campana_id` NULL, en vez de perderse:
    # así la suma cierra contra `CONECTADOS`.
    CONECTADOS_POR_CAMPANA = """
        WITH u AS (
            SELECT usuario, MAX(nomina_id) AS nomina_id
            FROM dbo.usuarios
            GROUP BY usuario
        ), ag AS (
            SELECT a.INTERVALO AS Intervalo, a.[LOGIN ID] AS login, u.nomina_id,
                   MAX(CAST(a.[LOGIN (s)] AS float)) AS seg,
                   MAX(CAST(a.[TIEMPO REAL DE LOGUEO] AS float)) AS seg_linea
            FROM dbo.acumuladores_de_agentes_por_skill a
            LEFT JOIN u ON u.usuario = a.[LOGIN ID]
            WHERE a.CAMPAÑA = 'HIDRAIN'
              AND a.FECHA >= :desde AND a.FECHA < :hasta
            GROUP BY a.INTERVALO, a.[LOGIN ID], u.nomina_id
        )
        SELECT ag.Intervalo AS momento, x.campana_id,
               SUM(ag.seg) / 1800.0 AS agentes,
               SUM(ag.seg_linea) / 1800.0 AS agentes_en_linea
        FROM ag
        OUTER APPLY (
            SELECT TOP 1 o.campana_id
            FROM dbo.payroll p
            JOIN dbo.operadores o ON o.id = p.id_operadores
            JOIN dbo.nomina n ON n.id = o.legajo_id
            WHERE n.documento = ag.nomina_id
              AND p.fecha IN (CAST(ag.Intervalo AS date),
                              DATEADD(day, -1, CAST(ag.Intervalo AS date)))
            ORDER BY CASE WHEN ag.Intervalo >= p.inicio AND ag.Intervalo < p.final
                          THEN 0 ELSE 1 END,
                     p.fecha DESC, p.horas_programadas DESC
        ) x
        GROUP BY ag.Intervalo, x.campana_id
    """

    # Rinde de la gente nueva. A diferencia de Voltara, el detalle de Mitrol trae el
    # LOGIN de quien atendió, así que el cruce a la nómina es por usuario y no por
    # nombre. Segundos por llamada = Talking + Hold + ACW, la misma definición que
    # el AHT de la serie (185 s contra 181 s el 14/09).
    RINDE_POR_ANTIGUEDAD = """
        WITH u AS (
            SELECT usuario, MAX(nomina_id) AS nomina_id
            FROM dbo.usuarios
            GROUP BY usuario
        ), llamadas AS (
            SELECT d.idCampania AS skill_id,
                   d.TalkingTime + d.Hold + d.ACW AS segundos,
                   DATEDIFF(day, (SELECT MAX(n.fecha_piso) FROM dbo.nomina n
                                  WHERE n.documento = u.nomina_id),
                            CAST(d.Inicio AS date)) AS dias
            FROM dbo.detalle_de_interacciones_por_agente d
            JOIN u ON u.usuario = d.LoginId
            WHERE d.Campaña = 'HIDRAIN' AND d.Atendidas = 1
              AND d.Inicio >= :desde AND d.Inicio < :hasta
              AND d.TalkingTime + d.Hold + d.ACW > 0
        )
        SELECT skill_id, dias, COUNT(*) AS llamadas,
               SUM(CAST(segundos AS bigint)) AS segundos
        FROM (
            SELECT skill_id, segundos,
                   CASE WHEN dias < 0 THEN 0 WHEN dias > :antiguo THEN :antiguo
                        ELSE dias END AS dias
            FROM llamadas
        ) x
        GROUP BY skill_id, dias
    """

    LLAMADAS_POR_ANTIGUEDAD = """
        WITH u AS (
            SELECT usuario, MAX(nomina_id) AS nomina_id
            FROM dbo.usuarios
            GROUP BY usuario
        ), d AS (
            SELECT CAST(a.Inicio AS date) AS fecha, u.nomina_id, COUNT(*) AS llamadas
            FROM dbo.detalle_de_interacciones_por_agente a
            JOIN u ON u.usuario = a.LoginId
            WHERE a.Campaña = 'HIDRAIN' AND a.Atendidas = 1
              AND a.Inicio >= :desde AND a.Inicio < :hasta
            GROUP BY CAST(a.Inicio AS date), u.nomina_id
        ), con_piso AS (
            SELECT d.llamadas,
                   DATEDIFF(day, (SELECT MAX(n.fecha_piso) FROM dbo.nomina n
                                  WHERE n.documento = d.nomina_id), d.fecha) AS dias
            FROM d
        )
        SELECT dias, SUM(llamadas) AS llamadas
        FROM (
            SELECT llamadas,
                   CASE WHEN dias < 0 THEN 0 WHEN dias > :antiguo THEN :antiguo
                        ELSE dias END AS dias
            FROM con_piso
        ) x
        GROUP BY dias
    """

    # El nivel de servicio real a 20 s. `eficiencia_de_campana` lo trae hecho pero
    # a 30 s (`TiempoUmbral` = 30 en HIDRAIN), así que el numerador se cuenta del
    # detalle por agente. Verificado el 14/09: con umbral 30 el detalle da 1.835 y
    # la tabla 1.829 (el borde, <= contra <); a 20 s son 1.693 de 2.512 entrantes.
    # El bucketeo va con fechas fijas y no con parámetros, así puede repetirse en
    # el GROUP BY (ver la trampa del error 8120 en el encabezado del módulo).
    CALIBRACION = """
        WITH pool AS (
            SELECT a.Intervalo,
                   SUM(CAST(a.Ingresadas AS int))                 AS entrantes,
                   SUM(CAST(a.AgentesAtendidas AS int))           AS atendidas,
                   SUM(CAST(a.AHT AS float) * CAST(a.AgentesAtendidas AS float)) AS tmo_x_llamadas,
                   SUM(CAST(a.Login AS float)) / 1800.0           AS agentes
            FROM dbo.Acumuladores_de_campana a
            WHERE a.Intervalo >= :desde AND a.Intervalo < :hasta
              AND a.idCampania IN :skills
            GROUP BY a.Intervalo
        ), rapidas AS (
            SELECT DATEADD(minute, (DATEDIFF(minute, '20000101', d.Inicio) / 30) * 30,
                           CAST('20000101' AS datetime)) AS Intervalo,
                   COUNT(*) AS hasta_20s
            FROM dbo.detalle_de_interacciones_por_agente d
            WHERE d.Inicio >= :desde AND d.Inicio < :hasta
              AND d.idCampania IN :skills
              AND d.Entrante = 1 AND d.Atendidas = 1 AND d.EnCola <= 20
            GROUP BY DATEADD(minute, (DATEDIFF(minute, '20000101', d.Inicio) / 30) * 30,
                             CAST('20000101' AS datetime))
        )
        SELECT p.Intervalo AS momento, p.entrantes, p.atendidas,
               ISNULL(r.hasta_20s, 0) AS hasta_20s, p.tmo_x_llamadas, p.agentes
        FROM pool p
        LEFT JOIN rapidas r ON r.Intervalo = p.Intervalo
        WHERE p.atendidas > 0 AND p.agentes > 0
        ORDER BY p.Intervalo
    """


# Cómo se saca el login de la columna `Agente` de Gasur ("calla519 (519)" -> "519"),
# igual que la vista dbo.Gasur_AHT. Es el `usuario` de dbo.usuarios.
_GASUR_LOGIN = ("SUBSTRING({c}, CHARINDEX('(', {c}) + 1, "
                 "LEN({c}) - CHARINDEX('(', {c}) - 1)")

# Las sesiones de dbo.[Gasur Actividad] partidas en medias horas: (agente, login,
# día de la sesión, media hora, segundos logueados en ella).
#
# LOS LOGOUT VIENEN DESORDENADOS. Con varias sesiones en el día, el reporte le
# pega a cada login el logout de OTRA sesión: el 02/07/2026 el 529 figura
# 10:00->00:00, 12:12->11:02 y 16:00->14:13, cuando fue 10:00->11:02,
# 12:12->14:13 y 16:00->00:00. Leídas fila por fila, esa semana daba 750 h contra
# 998 del tablero (que las lee igual de mal, en la otra dirección). Por eso los
# logins y los logouts del agente en el día se ordenan por separado y se aparean
# por orden. Un logout hasta las 02:00 es el fin de un turno que cruzó la
# medianoche (18:00 -> 00:00:01) y va al final. El par que aun así queda al revés
# es basura del reporte (19:44:47 -> 19:44:39) y se descarta.
#
# Logout NULL: 6 filas, todas de días cerrados; sin fin no hay duración.
# 'supapoyo5000' es el usuario de apoyo que la vista del tablero también saca.
# La media hora sale de fechas fijas y no de parámetros (error 8120, ver arriba).
_GASUR_TRAMOS = """
    act AS (
        SELECT a.Agente, a.Fecha, a.Login, a.Logout,
               CASE WHEN a.Logout <= '02:00' THEN 1 ELSE 0 END AS al_dia_siguiente
        FROM dbo.[Gasur Actividad] a
        WHERE a.Fecha >= DATEADD(day, -1, :desde) AND a.Fecha < :hasta
          AND a.Agente LIKE '%(%)' AND a.Agente <> 'supapoyo5000 (5000)'
          AND a.Login IS NOT NULL AND a.Logout IS NOT NULL
    ), entradas AS (
        SELECT Agente, Fecha, CAST(Fecha AS datetime) + CAST(Login AS datetime) AS ini,
               ROW_NUMBER() OVER (PARTITION BY Agente, Fecha ORDER BY Login) AS k
        FROM act
    ), salidas AS (
        SELECT Agente, Fecha,
               DATEADD(day, al_dia_siguiente, CAST(Fecha AS datetime))
                 + CAST(Logout AS datetime) AS fin,
               ROW_NUMBER() OVER (PARTITION BY Agente, Fecha
                                  ORDER BY al_dia_siguiente, Logout) AS k
        FROM act
    ), sesiones AS (
        SELECT e.Agente, """ + _GASUR_LOGIN.format(c="e.Agente") + """ AS login, e.Fecha,
               e.ini, s.fin
        FROM entradas e
        JOIN salidas s ON s.Agente = e.Agente AND s.Fecha = e.Fecha AND s.k = e.k
        WHERE s.fin > e.ini
    ), paso AS (
        SELECT d.n * 10 + u.n AS k
        FROM (VALUES (0), (1), (2), (3), (4)) d(n)
        CROSS JOIN (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)) u(n)
    ), tramos AS (
        SELECT s.Agente, s.login, s.Fecha, x.momento,
               DATEDIFF(second,
                        CASE WHEN s.ini > x.momento THEN s.ini ELSE x.momento END,
                        CASE WHEN s.fin < DATEADD(minute, 30, x.momento) THEN s.fin
                             ELSE DATEADD(minute, 30, x.momento) END) AS seg
        FROM sesiones s
        CROSS JOIN paso p
        CROSS APPLY (SELECT DATEADD(minute,
                                    (DATEDIFF(minute, '20000101', s.ini) / 30 + p.k) * 30,
                                    CAST('20000101' AS datetime)) AS momento) x
        WHERE x.momento < s.fin AND x.momento >= :desde AND x.momento < :hasta
    ), por_agente AS (
        -- Dos sesiones que se pisan (relogueo) no cuentan doble.
        SELECT Agente, login, Fecha, momento,
               CASE WHEN SUM(seg) > 1800 THEN 1800 ELSE SUM(seg) END AS seg
        FROM tramos
        GROUP BY Agente, login, Fecha, momento
    )"""

# La persona detrás de un login ese día. Los logins de Gasur (500-531) están
# MUY reciclados: la semana del 14/09 el 513 era de 6 personas en dbo.usuarios.
# Por eso no se deduplica por usuario (como en Hidra) sino que se elige la que
# tenía turno en payroll ese día —o el anterior, para el turno que cruza la
# medianoche—: primero la de Gasur (121) o Despacho (135), que son las que usan
# esta central, y entre ellas la que estaba dentro del turno a esa hora. Sin la
# preferencia, un 5xx reciclado caía en alguien de Hidra que casualmente tenía
# turno (18,7 h la semana del 01/07/2025).
_GASUR_PERSONA = """
    OUTER APPLY (
        SELECT TOP 1 o.campana_id, n.fecha_piso
        FROM dbo.usuarios u
        JOIN dbo.nomina n ON n.documento = u.nomina_id
        JOIN dbo.operadores o ON o.legajo_id = n.id
        JOIN dbo.payroll p ON p.id_operadores = o.id
        WHERE u.usuario = {login}
          AND p.fecha IN (CAST({momento} AS date), DATEADD(day, -1, CAST({momento} AS date)))
        ORDER BY CASE WHEN o.campana_id IN (121, 135) THEN 0 ELSE 1 END,
                 CASE WHEN {momento} >= p.inicio AND {momento} < p.final THEN 0 ELSE 1 END,
                 p.fecha DESC, p.horas_programadas DESC
    ) x"""


class FuenteGasur:
    """Gasur (Uruguay): llamada por llamada desde 2024-05, carga intradía.

    UNA SOLA COLA. El reporte trae 12 colas (C100C, C103C, C106C, CP231C...) que
    son números de entrada de distintas zonas, pero las atiende la misma gente:
    un skill (`SKILL_ID`, sintético) y un pool. El despacho (C500, en
    `Gasur Llamadas despacho`) es otra operación, de los radio-operadores, y no
    entra.

    DE DÓNDE SALE CADA COSA (verificado el 22/09/2026):
      - Volumen, atendidas, TMO, espera y abandono: `dbo.[Gasur Llamadas]`, una
        fila por llamada (Tipo 'Entrante'). Hora por hora da EXACTAMENTE el Total,
        las Completadas y las Abandonadas de `Gasur Eficiencia`. El `Id` no es
        único (es un float que colisiona): se cuentan filas.
      - El TMO es `Duración (seg.)` de las atendidas, que es sólo conversación: el
        reporte no trae ACW. Si lo hay, lo absorbe la disponibilidad calibrada.
      - Conectados: `dbo.[Gasur Actividad]` (login/logout por agente) partido en
        medias horas. Carga con el DÍA CERRADO: hoy no tiene conectados.
      - Pausas: `dbo.[Gasur Auxiliares]` trae los segundos por agente y día
        (Tareas, Varios, 30 y 10 minutos), no por intervalo. Los conectados "en
        línea" reparten esa pausa pareja en el día de cada agente.

    NIVEL DE SERVICIO A 10 s: es el umbral del `Nivel de Servicio %` de
    `Gasur Eficiencia` (atendidas en <= 10 s / entrantes; 15 h del 22/09: 58 de
    72 = 80,56%, igual que la tabla) y del Tablero_Gasur.

    ES NUESTRA PORCIÓN (ver el encabezado del módulo), aunque acá es todo lo que
    atiende el cliente por teléfono. Estacionalidad muy fuerte: es gas para
    calefacción (ene-2026 7.500 entrantes, jun-2026 45.000).
    """

    SKILL_ID = 1
    UMBRAL_REAL_SEG = 10

    _MEDIA_HORA = ("DATEADD(minute, (DATEDIFF(minute, '20000101', l.Fecha) / 30) * 30, "
                   "CAST('20000101' AS datetime))")

    SERIE = """
        SELECT """ + _MEDIA_HORA + """ AS momento,
               1 AS skill_id,
               COUNT(*) AS llamadas,
               SUM(CASE WHEN l.Estado <> 'Abandonada' THEN 1 ELSE 0 END) AS atendidas,
               SUM(CASE WHEN l.Estado <> 'Abandonada'
                        THEN CAST(l.[Duración (seg.)] AS float) ELSE 0 END) AS tmo_x_llamadas
        FROM dbo.[Gasur Llamadas] l
        WHERE l.Tipo = 'Entrante'
          AND l.Fecha >= :desde AND l.Fecha < :hasta
        GROUP BY """ + _MEDIA_HORA + """
        ORDER BY momento
    """

    # `skill` con el nombre de planificacion.Skill, que es por lo que se cruza.
    PACIENCIA = """
        SELECT 'Gasur' AS skill,
               SUM(CASE WHEN l.Estado = 'Abandonada' THEN 1 ELSE 0 END) AS abandonos,
               SUM(CAST(l.[Espera (seg.)] AS bigint))                  AS espera_total
        FROM dbo.[Gasur Llamadas] l
        WHERE l.Tipo = 'Entrante' AND l.[Espera (seg.)] >= 0
          AND l.Fecha >= :desde AND l.Fecha < :hasta
    """

    PACIENCIA_CURVA = """
        SELECT 'Gasur' AS skill, bucket,
               SUM(CASE WHEN abandonada = 1 THEN 1 ELSE 0 END) AS abandonos,
               SUM(CASE WHEN abandonada = 0 THEN 1 ELSE 0 END) AS atendidas
        FROM (
            SELECT CASE WHEN l.[Espera (seg.)] >= :tope THEN :tope
                        ELSE (l.[Espera (seg.)] / :paso) * :paso END AS bucket,
                   CASE WHEN l.Estado = 'Abandonada' THEN 1 ELSE 0 END AS abandonada
            FROM dbo.[Gasur Llamadas] l
            WHERE l.Tipo = 'Entrante' AND l.[Espera (seg.)] >= 0
              AND l.Fecha >= :desde AND l.Fecha < :hasta
        ) t
        GROUP BY bucket
        ORDER BY bucket
    """

    CONECTADOS = "WITH" + _GASUR_TRAMOS + """
        SELECT momento, SUM(CAST(seg AS float)) / 1800.0 AS agentes
        FROM por_agente
        GROUP BY momento
    """

    # Los conectados partidos por la sub-campaña de RRHH de ESE día (como en
    # Voltara y Hidra). Un login sin persona con turno queda con `campana_id` NULL
    # en vez de perderse, así la suma cierra contra `CONECTADOS`.
    CONECTADOS_POR_CAMPANA = "WITH" + _GASUR_TRAMOS + """, pausa AS (
            SELECT a.Agente, a.Fecha,
                   CAST(ISNULL(a.Tareas, 0) + ISNULL(a.Varios, 0)
                        + ISNULL(a.[30 minutos], 0) + ISNULL(a.[10 minutos], 0) AS float) AS seg
            FROM dbo.[Gasur Auxiliares] a
            WHERE a.Fecha >= DATEADD(day, -1, :desde) AND a.Fecha < :hasta
        ), logueo AS (
            SELECT Agente, Fecha, CAST(SUM(DATEDIFF(second, ini, fin)) AS float) AS seg
            FROM sesiones
            GROUP BY Agente, Fecha
        ), ag AS (
            SELECT pa.momento, pa.login, pa.seg,
                   CASE WHEN l.seg > 0 AND p.seg IS NOT NULL
                        THEN 1.0 - CASE WHEN p.seg > l.seg THEN 1.0 ELSE p.seg / l.seg END
                        ELSE 1.0 END AS en_linea
            FROM por_agente pa
            LEFT JOIN logueo l ON l.Agente = pa.Agente AND l.Fecha = pa.Fecha
            LEFT JOIN (SELECT Agente, Fecha, SUM(seg) AS seg FROM pausa GROUP BY Agente, Fecha) p
                   ON p.Agente = pa.Agente AND p.Fecha = pa.Fecha
        )
        SELECT ag.momento, x.campana_id,
               SUM(CAST(ag.seg AS float)) / 1800.0 AS agentes,
               SUM(CAST(ag.seg AS float) * ag.en_linea) / 1800.0 AS agentes_en_linea
        FROM ag""" + _GASUR_PERSONA.format(login="ag.login", momento="ag.momento") + """
        GROUP BY ag.momento, x.campana_id
    """

    # Rinde de la gente nueva: la llamada lleva el login de quien atendió y se
    # cruza a la persona por el turno de ese día (ver `_GASUR_PERSONA`). Se
    # agrupa por login y día antes del cruce: es la misma cuenta y resuelve la
    # persona una vez por día en vez de una por llamada.
    _POR_LOGIN_Y_DIA = """
        WITH ll AS (
            SELECT CAST(l.Fecha AS date) AS fecha,
                   """ + _GASUR_LOGIN.format(c="l.Agente") + """ AS login,
                   CAST(l.[Duración (seg.)] AS bigint) AS seg
            FROM dbo.[Gasur Llamadas] l
            WHERE l.Tipo = 'Entrante' AND l.Estado <> 'Abandonada'
              AND l.Agente LIKE '%(%)' AND l.[Duración (seg.)] > 0
              AND l.Fecha >= :desde AND l.Fecha < :hasta
        ), d AS (
            SELECT fecha, login, COUNT(*) AS llamadas, SUM(seg) AS segundos,
                   DATEADD(hour, 12, CAST(fecha AS datetime)) AS momento
            FROM ll
            GROUP BY fecha, login
        ), con_piso AS (
            SELECT d.llamadas, d.segundos, DATEDIFF(day, x.fecha_piso, d.fecha) AS dias
            FROM d""" + _GASUR_PERSONA.format(login="d.login", momento="d.momento") + """
            WHERE x.fecha_piso IS NOT NULL
        )"""

    RINDE_POR_ANTIGUEDAD = _POR_LOGIN_Y_DIA + """
        SELECT 1 AS skill_id, dias, SUM(llamadas) AS llamadas, SUM(segundos) AS segundos
        FROM (
            SELECT llamadas, segundos,
                   CASE WHEN dias < 0 THEN 0 WHEN dias > :antiguo THEN :antiguo
                        ELSE dias END AS dias
            FROM con_piso
        ) x
        GROUP BY dias
    """

    LLAMADAS_POR_ANTIGUEDAD = _POR_LOGIN_Y_DIA + """
        SELECT dias, SUM(llamadas) AS llamadas
        FROM (
            SELECT llamadas,
                   CASE WHEN dias < 0 THEN 0 WHEN dias > :antiguo THEN :antiguo
                        ELSE dias END AS dias
            FROM con_piso
        ) x
        GROUP BY dias
    """

    # El nivel de servicio real a 10 s (`UMBRAL_REAL_SEG`; la columna se sigue
    # llamando `hasta_20s` porque es la que leen la calibración y el servicio
    # real). Filas sin conectados se devuelven igual: hoy no hay conectados
    # (Actividad carga con el día cerrado) y el servicio real de hoy tiene que
    # verse; la calibración ya saltea los intervalos sin gente.
    CALIBRACION = "WITH" + _GASUR_TRAMOS + """, pool AS (
            SELECT """ + _MEDIA_HORA + """ AS momento,
                   COUNT(*) AS entrantes,
                   SUM(CASE WHEN l.Estado <> 'Abandonada' THEN 1 ELSE 0 END) AS atendidas,
                   SUM(CASE WHEN l.Estado <> 'Abandonada' AND l.[Espera (seg.)] <= 10
                            THEN 1 ELSE 0 END) AS hasta_20s,
                   SUM(CASE WHEN l.Estado <> 'Abandonada'
                            THEN CAST(l.[Duración (seg.)] AS float) ELSE 0 END) AS tmo_x_llamadas
            FROM dbo.[Gasur Llamadas] l
            WHERE l.Tipo = 'Entrante' AND 1 IN :skills
              AND l.Fecha >= :desde AND l.Fecha < :hasta
            GROUP BY """ + _MEDIA_HORA + """
        ), gente AS (
            SELECT momento, SUM(CAST(seg AS float)) / 1800.0 AS agentes
            FROM por_agente
            GROUP BY momento
        )
        SELECT p.momento, p.entrantes, p.atendidas, p.hasta_20s, p.tmo_x_llamadas,
               ISNULL(g.agentes, 0) AS agentes
        FROM pool p
        LEFT JOIN gente g ON g.momento = p.momento
        WHERE p.atendidas > 0
        ORDER BY p.momento
    """


FUENTES = {CAMPANA_VOLTARA: FuenteVoltara, CAMPANA_HIDRA_TECNICO: FuenteHidraTecnico,
           CAMPANA_GASUR: FuenteGasur}

# Cómo se llama cada campaña dentro de dbo.Forecast, que es el pronóstico que la
# operación usa HOY (el XGBoost viejo). No es una fuente más: es la referencia
# contra la que hay que medirse. Un modelo nuevo que no le gana al que ya está
# andando no se pone en producción, y sin tenerlo al lado en la misma pantalla la
# discusión se vuelve una cuestión de impresiones.
CAMPANA_EN_FORECAST = {CAMPANA_VOLTARA: "Voltara", CAMPANA_HIDRA_TECNICO: "Hidra",
                       CAMPANA_GASUR: "Gasur"}

# Las campañas que en dbo.Forecast vienen sin skill y por HORA en vez de por media
# hora: campaña -> (skill al que se le atribuye, minutos de cada fila). El de Hidra
# es de HIDRAIN (2.187 pronosticadas el 21/09 contra 2.147 reales), una fila por
# hora y `Skill ID` en NULL. Sin esto se descartaba entero. El de Gasur viene
# igual (una fila por hora de 08 a 23, sin skill).
FORECAST_SIN_SKILL = {CAMPANA_HIDRA_TECNICO: (481, 60),
                      CAMPANA_GASUR: (FuenteGasur.SKILL_ID, 60)}

# Nombre en el selector cuando el de calidad.Campanas no alcanza: Hidra tiene dos
# campañas que son dos operaciones distintas, y "HidraIN" (el nombre en Calidad) no
# dice cuál de las dos es.
NOMBRE_EN_PLANIFICADOR = {CAMPANA_HIDRA_TECNICO: "Hidra Técnico", CAMPANA_GASUR: "Gasur"}

# Cómo se llama en la pantalla la gente que se puede pasar a la línea cuando no se
# llega (planificacion.PoolRefuerzo). En Voltara son las sub-campañas de Digital; una
# campaña sin nombre cargado acá lo ve como "Refuerzo".
ETIQUETA_REFUERZO = {CAMPANA_VOLTARA: "Digital", CAMPANA_GASUR: "Despacho"}

TABLA_FORECAST = "dbo.Forecast"

_FORECAST = """
    SELECT Fecha, Intervalo, [Skill ID] AS skill_id,
           SUM(CAST(Forecast AS int)) AS llamadas
    FROM dbo.Forecast
    WHERE [Campaña] = :campana AND Fecha >= :desde AND Fecha < :hasta
    GROUP BY Fecha, Intervalo, [Skill ID]
"""


def hay_forecast_en_produccion(conn: Connection, campana_id: int) -> bool:
    return (campana_id in CAMPANA_EN_FORECAST
            and _tiene_tabla(conn, TABLA_FORECAST))


def forecast_en_produccion(conn: Connection, campana_id: int, desde: date,
                           hasta: date) -> Dict[Tuple[datetime, int], float]:
    """(momento, skill) -> llamadas que pronosticó dbo.Forecast. `hasta` exclusivo.

    Ojo con qué pronostica: son NUESTRAS llamadas, no la demanda del cliente. O
    sea que ya tiene el reparto adentro, y por eso se compara contra el real de
    Acme y nunca contra el total del IVR.
    """
    nombre = CAMPANA_EN_FORECAST.get(campana_id)
    if not nombre:
        return {}
    filas = conn.execute(text(_FORECAST),
                         {"campana": nombre, "desde": desde, "hasta": hasta}).mappings()
    return forecast_por_intervalo(filas, FORECAST_SIN_SKILL.get(campana_id))


def forecast_por_intervalo(filas, sin_skill: Optional[Tuple[int, int]] = None,
                           intervalo_min: int = 30) -> Dict[Tuple[datetime, int], float]:
    """Las filas de dbo.Forecast a (momento, skill) -> llamadas, por media hora.

    `sin_skill` = (skill, minutos de cada fila) para las campañas que el cliente
    pronostica sin skill y por hora (Hidra): la fila sin skill se le atribuye a ese
    skill y la hora se reparte EN PARTES IGUALES entre sus medias horas. Es un
    reparto ingenuo a propósito —repartirla con nuestra curva le prestaría al
    pronóstico del cliente lo que sabe el nuestro—, así que en la comparación por
    media hora el del cliente carga un poco de error de más en las horas de rampa;
    en el total del día no cambia nada.
    """
    skill_def, minutos = sin_skill if sin_skill else (None, intervalo_min)
    partes = max(1, minutos // intervalo_min)
    salida: Dict[Tuple[datetime, int], float] = {}
    for f in filas:
        if f["Fecha"] is None or f["Intervalo"] is None:
            continue
        skill = f["skill_id"] if f["skill_id"] is not None else skill_def
        if skill is None:
            continue
        inicio = datetime.combine(f["Fecha"], f["Intervalo"])
        valor = float(f["llamadas"] or 0) / partes
        for i in range(partes):
            clave = (inicio + timedelta(minutes=i * intervalo_min), int(skill))
            salida[clave] = salida.get(clave, 0.0) + valor
    return salida


def _fuente(campana_id: int):
    fuente = FUENTES.get(campana_id)
    if fuente is None:
        raise ValueError(
            f"La campaña {campana_id} todavía no tiene fuente de datos declarada "
            f"en planificador_datos.FUENTES."
        )
    return fuente


# --------------------------------------------------------------- configuración

def schema_disponible(conn: Connection) -> bool:
    """Si la migración 2026-09-03 todavía no corrió, la pantalla lo avisa en vez
    de devolver un 500 sin explicación."""
    fila = conn.execute(text(
        "SELECT 1 FROM sys.schemas WHERE name = 'planificacion'")).fetchone()
    return fila is not None


def cargar_config(conn: Connection, campana_id: int) -> pl.CampanaCfg:
    """Arma la configuración completa de una campaña."""
    if not schema_disponible(conn):
        raise MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-03_planificador.sql")

    # Las columnas de calibración las agrega la migración 2026-09-03b. Se leen
    # aparte para que una base con la primera migración pero sin la segunda siga
    # funcionando (sin trazabilidad, pero sin romperse).
    extra = ("PacienciaHorizonteSeg, PacienciaOrigen, ShrinkageAusentismo, "
             "ShrinkageCapacitacion, ShrinkageOrigen, ShrinkageMedidoEn")
    if not _tiene_columna(conn, "planificacion.Campana", "PacienciaHorizonteSeg"):
        extra = ("NULL AS PacienciaHorizonteSeg, NULL AS PacienciaOrigen, "
                 "NULL AS ShrinkageAusentismo, NULL AS ShrinkageCapacitacion, "
                 "NULL AS ShrinkageOrigen, NULL AS ShrinkageMedidoEn")
    # La ventana de entrenamiento la agrega la migración 2026-09-07c. Sin ella se
    # cae a los valores del dataclass, que son los medidos.
    if _tiene_columna(conn, "planificacion.Campana", "SemanasBase"):
        extra += ", SemanasBase, DiasNivelReciente"
    else:
        extra += ", NULL AS SemanasBase, NULL AS DiasNivelReciente"
    # La agrega la migración 2026-09-08, que es POSTERIOR a la de la ventana: se
    # pregunta aparte y no junto con SemanasBase porque una base puede tener la
    # primera y no la segunda, que es exactamente lo que pasó al desarrollarla.
    if _tiene_columna(conn, "planificacion.Campana", "NivelPorTipoDeDia"):
        extra += ", NivelPorTipoDeDia"
    else:
        extra += ", NULL AS NivelPorTipoDeDia"
    # La deriva del reparto la agrega la migración 2026-09-08b. Guard propio por
    # lo mismo que arriba: cada columna se pregunta por su cuenta.
    if _tiene_columna(conn, "planificacion.Campana", "RepartoDerivaDias"):
        extra += ", RepartoDerivaDias, RepartoDerivaTope"
    else:
        extra += ", NULL AS RepartoDerivaDias, NULL AS RepartoDerivaTope"
    # El reparto por tipo de día y la combinación con el pronóstico del cliente
    # los agrega la migración 2026-09-09. Mismo guard por columna que arriba.
    if _tiene_columna(conn, "planificacion.Campana", "RepartoTipoDiaDias"):
        extra += (", RepartoTipoDiaDias, RepartoTipoDiaTope, CombinarCliente, "
                  "CombinarClientePesoHabil, CombinarClientePesoNoHabil, "
                  "CombinarClienteMedidoEn")
    else:
        extra += (", NULL AS RepartoTipoDiaDias, NULL AS RepartoTipoDiaTope, "
                  "NULL AS CombinarCliente, NULL AS CombinarClientePesoHabil, "
                  "NULL AS CombinarClientePesoNoHabil, "
                  "NULL AS CombinarClienteMedidoEn")
    # La segunda opinión del nivel la agrega la migración 2026-09-09c. Guard por
    # columna, como todas: una base puede tener las anteriores y no ésta.
    if _tiene_columna(conn, "planificacion.Campana", "NivelGbdt"):
        extra += ", NivelGbdt, NivelGbdtPeso"
    else:
        extra += ", NULL AS NivelGbdt, NULL AS NivelGbdtPeso"
    # La elasticidad del domingo la agrega la migración 2026-09-09d. Guard
    # propio: la 09c puede estar aplicada y ésta no.
    if _tiene_columna(conn, "planificacion.Campana", "ClimaElasticidadTipoDia"):
        extra += ", ClimaElasticidadTipoDia"
    else:
        extra += ", NULL AS ClimaElasticidadTipoDia"
    # El shrinkage por tipo de día y el break los agrega la 2026-09-10.
    if _tiene_columna(conn, "planificacion.Campana", "ShrinkageFeriado"):
        extra += ", ShrinkageNoHabil, ShrinkageFeriado, BreakMinPorHora"
    else:
        extra += (", NULL AS ShrinkageNoHabil, NULL AS ShrinkageFeriado, "
                  "NULL AS BreakMinPorHora")
    # El calendario propio, la persistencia y el reescalado intradía los agrega
    # la 2026-09-22 (alta de Hidra Técnico).
    if _tiene_columna(conn, "planificacion.Campana", "PersistenciaPesoHoy"):
        extra += (", FeriadoComoSabado, PuenteFactor, PersistenciaPesoHoy, "
                  "PersistenciaPesoResto, PersistenciaDias, IntradiaDesdeHora")
    else:
        extra += (", NULL AS FeriadoComoSabado, NULL AS PuenteFactor, NULL AS PersistenciaPesoHoy, "
                  "NULL AS PersistenciaPesoResto, NULL AS PersistenciaDias, "
                  "NULL AS IntradiaDesdeHora")
    # La forma reciente y la persistencia que saltea eventos las agrega la
    # 2026-09-24e (Gasur).
    if _tiene_columna(conn, "planificacion.Campana", "FormaDias"):
        extra += (", FormaDias, PersistenciaSalteaEventos, AnclaMensualPeso, "
                  "AnclaMensualDesdeDias")
    else:
        extra += (", NULL AS FormaDias, NULL AS PersistenciaSalteaEventos, "
                  "NULL AS AnclaMensualPeso, NULL AS AnclaMensualDesdeDias")
    # La franja de redondeo para abajo la agrega la 2026-09-17.
    if _tiene_columna(conn, "planificacion.Campana", "RedondeoAbajoDesde"):
        extra += ", RedondeoAbajoDesde, RedondeoAbajoHasta"
    else:
        extra += ", NULL AS RedondeoAbajoDesde, NULL AS RedondeoAbajoHasta"
    cab = conn.execute(text(f"""
        SELECT IntervaloMin, MaxOcupacion, ShrinkageDefault, PacienciaSeg, Activa,
               {extra}
        FROM planificacion.Campana WHERE CampanaID = :c
    """), {"c": campana_id}).mappings().fetchone()
    if not cab:
        raise ValueError(f"La campaña {campana_id} no está dada de alta en el planificador.")

    origenes = _origen_de_los_pools(conn, campana_id)
    pools = {
        f["PoolID"]: pl.PoolCfg(pool_id=f["PoolID"], nombre=f["Nombre"],
                                min_operadores=int(f["MinOperadores"] or 0),
                                activo=bool(f["Activo"]),
                                origen_rrhh=origenes.get(f["PoolID"], []))
        for f in conn.execute(text("""
            SELECT PoolID, Nombre, MinOperadores, Activo
            FROM planificacion.Pool WHERE CampanaID = :c AND Activo = 1
        """), {"c": campana_id}).mappings()
    }

    skills = [
        pl.SkillCfg(
            skill_id=f["SkillID"], nombre=f["Nombre"], pool_id=f["PoolID"],
            objetivo_nds=float(f["ObjetivoNds"]) if f["ObjetivoNds"] is not None else None,
            umbral_seg=int(f["UmbralSeg"]) if f["UmbralSeg"] is not None else None,
            max_abandono=float(f["MaxAbandono"]) if f["MaxAbandono"] is not None else None,
            paciencia_seg=_int_o_none(f["PacienciaSeg"]),
            max_asa_seg=_int_o_none(f["MaxAsaSeg"]),
            objetivo_nds_2=_float_o_none(f["ObjetivoNds2"]),
            umbral_seg_2=_int_o_none(f["UmbralSeg2"]),
            min_nivel_atencion_b=_float_o_none(f["MinNivelAtencionB"]),
            prioridad=bool(f["Prioridad"]),
            activo=bool(f["Activo"]),
        )
        for f in conn.execute(text(f"""
            SELECT SkillID, Nombre, PoolID, ObjetivoNds, UmbralSeg, MaxAbandono,
                   PacienciaSeg, Activo, {_columnas_planilla(conn)}
            FROM planificacion.Skill WHERE CampanaID = :c
        """), {"c": campana_id}).mappings()
    ]

    tiene_origen_disp = _tiene_columna(conn, "planificacion.Disponibilidad", "Origen")
    extra_disp = ", Origen" if tiene_origen_disp else ""
    disponibilidad = []
    for f in conn.execute(text(f"""
        SELECT DiaSemana, HoraDesde, HoraHasta, Factor{extra_disp}
        FROM planificacion.Disponibilidad
        WHERE CampanaID = :c
        ORDER BY DiaSemana DESC, HoraDesde
    """), {"c": campana_id}).mappings():
        franja = pl.FranjaDisponibilidad(int(f["DiaSemana"]), int(f["HoraDesde"]),
                                         int(f["HoraHasta"]), float(f["Factor"]))
        if tiene_origen_disp and f.get("Origen") is not None:
            franja.origen = f["Origen"]
        disponibilidad.append(franja)

    perfil, perfil_medido_en = perfil_presencia(conn, campana_id)
    filas_curva = leer_curva_antiguedad(conn, campana_id)
    return pl.CampanaCfg(
        campana_id=campana_id,
        perfil_presencia=perfil, perfil_presencia_medido_en=perfil_medido_en,
        curva_antiguedad=pant.como_curva(filas_curva),
        curva_antiguedad_medido_en=max((f["medido_en"] for f in filas_curva), default=None),
        intervalo_min=int(cab["IntervaloMin"]),
        max_ocupacion=float(cab["MaxOcupacion"]) if cab["MaxOcupacion"] is not None else None,
        shrinkage=float(cab["ShrinkageDefault"]),
        paciencia_seg=int(cab["PacienciaSeg"]) if cab["PacienciaSeg"] is not None else None,
        pools=pools, skills=skills, disponibilidad=disponibilidad,
        paciencia_horizonte_seg=_int_o_none(cab["PacienciaHorizonteSeg"]),
        paciencia_origen=cab["PacienciaOrigen"],
        shrinkage_ausentismo=_float_o_none(cab["ShrinkageAusentismo"]),
        shrinkage_capacitacion=_float_o_none(cab["ShrinkageCapacitacion"]),
        shrinkage_origen=cab["ShrinkageOrigen"],
        shrinkage_medido_en=cab["ShrinkageMedidoEn"],
        **{k: v for k, v in (("semanas_base", _int_o_none(cab["SemanasBase"])),
                             ("dias_nivel", _int_o_none(cab["DiasNivelReciente"])))
           if v},
        **({"nivel_por_tipo_de_dia": bool(cab["NivelPorTipoDeDia"])}
           if cab["NivelPorTipoDeDia"] is not None else {}),
        # El 0 es un valor legítimo (deriva apagada), así que acá no sirve el
        # filtro por verdad que usa la ventana de entrenamiento más arriba.
        **({"reparto_deriva_dias": int(cab["RepartoDerivaDias"])}
           if cab["RepartoDerivaDias"] is not None else {}),
        **({"reparto_deriva_tope": float(cab["RepartoDerivaTope"])}
           if cab["RepartoDerivaTope"] is not None else {}),
        **({"reparto_tipo_dia_dias": int(cab["RepartoTipoDiaDias"])}
           if cab["RepartoTipoDiaDias"] is not None else {}),
        **({"reparto_tipo_dia_tope": float(cab["RepartoTipoDiaTope"])}
           if cab["RepartoTipoDiaTope"] is not None else {}),
        **({"combinar_cliente": bool(cab["CombinarCliente"])}
           if cab["CombinarCliente"] is not None else {}),
        combinar_cliente_peso_habil=_float_o_none(cab["CombinarClientePesoHabil"]),
        combinar_cliente_peso_no_habil=_float_o_none(cab["CombinarClientePesoNoHabil"]),
        combinar_cliente_medido_en=cab["CombinarClienteMedidoEn"],
        **({"nivel_gbdt": bool(cab["NivelGbdt"])}
           if cab["NivelGbdt"] is not None else {}),
        **({"nivel_gbdt_peso": float(cab["NivelGbdtPeso"])}
           if cab["NivelGbdtPeso"] is not None else {}),
        **({"clima_elasticidad_tipo_dia": bool(cab["ClimaElasticidadTipoDia"])}
           if cab["ClimaElasticidadTipoDia"] is not None else {}),
        # Estos dos NO se filtran como los de arriba: el None es un valor con
        # significado —"usá el general"— y no la ausencia del dato.
        shrinkage_no_habil=_float_o_none(cab["ShrinkageNoHabil"]),
        shrinkage_feriado=_float_o_none(cab["ShrinkageFeriado"]),
        **({"break_min_por_hora": float(cab["BreakMinPorHora"])}
           if cab["BreakMinPorHora"] is not None else {}),
        redondeo_abajo_desde=_int_o_none(cab["RedondeoAbajoDesde"]),
        redondeo_abajo_hasta=_int_o_none(cab["RedondeoAbajoHasta"]),
        feriado_como_sabado=bool(cab["FeriadoComoSabado"]),
        puente_factor=_float_o_none(cab["PuenteFactor"]),
        persistencia_peso_hoy=_float_o_none(cab["PersistenciaPesoHoy"]) or 0.0,
        persistencia_peso_resto=_float_o_none(cab["PersistenciaPesoResto"]) or 0.0,
        persistencia_dias=_int_o_none(cab["PersistenciaDias"]) or 0,
        intradia_desde_hora=_int_o_none(cab["IntradiaDesdeHora"]),
        forma_dias=_int_o_none(cab["FormaDias"]),
        persistencia_saltea_eventos=bool(cab["PersistenciaSalteaEventos"]),
        ancla_mensual_peso=_float_o_none(cab["AnclaMensualPeso"]) or 0.0,
        ancla_mensual_desde_dias=_int_o_none(cab["AnclaMensualDesdeDias"]) or 7,
    )


# ------------------------------------------------------ perfil de presencia

def perfil_presencia(conn: Connection, campana_id: int
                     ) -> Tuple[Dict[Tuple[str, int], float], Optional[datetime]]:
    """El apartamiento por media hora que mide scripts/planificador_perfil_presencia.py.

    Sin la migración 2026-09-15b o sin medición: vacío, y el dimensionamiento queda
    como antes (shrinkage parejo en el día). Ver `planificador_presencia`.
    """
    if not _tiene_tabla(conn, "planificacion.PerfilPresencia"):
        return {}, None
    filas = conn.execute(text("""
        SELECT TipoDia, Minuto, Exceso, MedidoEn
        FROM planificacion.PerfilPresencia
        WHERE CampanaID = :c
    """), {"c": campana_id}).mappings().all()
    perfil = {(f["TipoDia"], int(f["Minuto"])): float(f["Exceso"]) for f in filas}
    return perfil, max((f["MedidoEn"] for f in filas), default=None)


# Desde cuándo el perfil se mide con pool + otras sub-campañas (en UTC, como
# `PerfilPresencia.MedidoEn`). Ver `planificador_presencia.conectados_que_cubren`.
PRESENCIA_CON_OTRAS_DESDE = datetime(2026, 9, 16)


def nivel_de_presencia(conn: Connection, campana_id: int) -> Dict[str, object]:
    """El faltante medio de cada tipo de día según el último perfil de presencia:
    es el shrinkage que propone la calibración.

    Sale de la misma tabla que la forma por media hora, así nivel y forma describen
    a la misma gente (ver `planificador_presencia.conectados_que_cubren`). Se lee
    lo guardado y no se vuelve a medir: la medición son 8 semanas de registro y de
    conectados, y la calibración ya es lenta.

    Vacío sin la migración 2026-09-15b o sin medición: la calibración sigue
    proponiendo el de los códigos de RRHH, como antes.
    """
    if not _tiene_tabla(conn, "planificacion.PerfilPresencia"):
        return {}
    filas = conn.execute(text("""
        SELECT TipoDia, Faltante, Muestras, MedidoEn
        FROM planificacion.PerfilPresencia
        WHERE CampanaID = :c
    """), {"c": campana_id}).mappings().all()
    medido_en = max((f["MedidoEn"] for f in filas), default=None)
    # UN PERFIL VIEJO NO SIRVE DE NIVEL. Hasta el 16/09 se medía sólo contra los
    # conectados del pool, y ese faltante (15,9% en hábiles) es la mitad de la
    # historia: proponerlo le sumaría ocho puntos a la dotación. La tabla no dice
    # con qué población se midió, así que decide la fecha.
    if medido_en is not None and medido_en < PRESENCIA_CON_OTRAS_DESDE:
        return {"motivo": "El perfil guardado se midió sólo con la gente del pool. "
                          "Hay que volver a correr scripts/planificador_perfil_presencia.py "
                          "(o esperar al lunes) para proponer el nivel.",
                "medido_en": medido_en}
    nivel = ppresencia.nivel_por_tipo(
        {"tipo_dia": f["TipoDia"], "faltante": f["Faltante"], "muestras": f["Muestras"]}
        for f in filas)
    if not nivel:
        return {}
    return {"por_tipo": nivel, "medido_en": medido_en, "semanas": ppresencia.SEMANAS}


def guardar_perfil_presencia(conn: Connection, campana_id: int,
                             filas: Sequence[dict]) -> int:
    """Reemplaza el perfil de la campaña entero, en la transacción del llamador:
    una medición a medias mezclaría medias horas de dos semanas distintas."""
    if not _tiene_tabla(conn, "planificacion.PerfilPresencia"):
        raise MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-15b_planificador_perfil_presencia.sql")
    conn.execute(text("DELETE FROM planificacion.PerfilPresencia WHERE CampanaID = :c"),
                 {"c": campana_id})
    if filas:
        conn.execute(text("""
            INSERT INTO planificacion.PerfilPresencia
                (CampanaID, TipoDia, Minuto, Exceso, Faltante, Muestras)
            VALUES (:c, :t, :m, :e, :f, :n)
        """), [{"c": campana_id, "t": f["tipo_dia"], "m": f["minuto"],
                "e": f["exceso"], "f": f["faltante"], "n": f["muestras"]} for f in filas])
    return len(filas)


# ------------------------------------------------------------------ antigüedad

def leer_curva_antiguedad(conn: Connection, campana_id: int) -> List[dict]:
    """La curva que mide scripts/planificador_antiguedad.py, de menor a mayor.

    Sin la migración 2026-09-15c o sin medición: vacía, y cada citado cuenta 1.
    """
    if not _tiene_tabla(conn, "planificacion.CurvaAntiguedad"):
        return []
    filas = conn.execute(text("""
        SELECT DiaDesde, DiaHasta, Rinde, Factor, Llamadas, Participacion, MedidoEn
        FROM planificacion.CurvaAntiguedad
        WHERE CampanaID = :c
        ORDER BY DiaDesde
    """), {"c": campana_id}).mappings().all()
    return [{"dia_desde": int(f["DiaDesde"]), "dia_hasta": _int_o_none(f["DiaHasta"]),
             "rinde": float(f["Rinde"]), "factor": float(f["Factor"]),
             "llamadas": int(f["Llamadas"] or 0),
             "participacion": _float_o_none(f["Participacion"]),
             "medido_en": f["MedidoEn"]} for f in filas]


def guardar_curva_antiguedad(conn: Connection, campana_id: int,
                             filas: Sequence[dict]) -> int:
    """Reemplaza la curva entera, en la transacción del llamador: los pesos están
    normalizados entre sí y una mitad vieja con otra nueva no suma lo que debe."""
    if not _tiene_tabla(conn, "planificacion.CurvaAntiguedad"):
        raise MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-15c_planificador_antiguedad.sql")
    conn.execute(text("DELETE FROM planificacion.CurvaAntiguedad WHERE CampanaID = :c"),
                 {"c": campana_id})
    if filas:
        conn.execute(text("""
            INSERT INTO planificacion.CurvaAntiguedad
                (CampanaID, DiaDesde, DiaHasta, Rinde, Factor, Llamadas, Participacion)
            VALUES (:c, :desde, :hasta, :rinde, :factor, :llamadas, :part)
        """), [{"c": campana_id, "desde": f["dia_desde"], "hasta": f["dia_hasta"],
                "rinde": f["rinde"], "factor": f["factor"], "llamadas": f["llamadas"],
                "part": f["participacion"]} for f in filas])
    return len(filas)


def rinde_por_antiguedad(conn: Connection, campana_id: int, desde: date,
                         hasta: date) -> List[dict]:
    """{skill_id, dias, llamadas, segundos}. Vacío si la fuente no lo sabe medir."""
    fuente = FUENTES.get(campana_id)
    if fuente is None or not hasattr(fuente, "RINDE_POR_ANTIGUEDAD"):
        return []
    return [dict(f) for f in conn.execute(
        text(fuente.RINDE_POR_ANTIGUEDAD),
        {"desde": desde, "hasta": hasta, "antiguo": pant.DIAS_ANTIGUO}).mappings()]


def llamadas_por_antiguedad(conn: Connection, campana_id: int, desde: date,
                            hasta: date) -> List[dict]:
    """{dias, llamadas}. Vacío si la fuente no lo sabe medir."""
    fuente = FUENTES.get(campana_id)
    if fuente is None or not hasattr(fuente, "LLAMADAS_POR_ANTIGUEDAD"):
        return []
    return [dict(f) for f in conn.execute(
        text(fuente.LLAMADAS_POR_ANTIGUEDAD),
        {"desde": desde, "hasta": hasta, "antiguo": pant.DIAS_ANTIGUO}).mappings()]


# ------------------------------------------------------------------ campañas

def campanas_del_planificador(conn: Connection) -> List[dict]:
    """Las campañas dadas de alta en planificacion.Campana, con su nombre y empresa.

    `con_fuente` dice si el código sabe de dónde leer sus llamadas (`FUENTES`): una
    campaña puede estar configurada antes de que exista su fuente, y entonces se
    puede mirar su configuración pero no pronosticar.
    """
    if not schema_disponible(conn):
        return []
    filas = conn.execute(text("""
        SELECT pc.CampanaID, pc.Activa, c.Nombre, c.EmpresaID
        FROM planificacion.Campana pc
        LEFT JOIN calidad.Campanas c ON c.CampanaID = pc.CampanaID
        ORDER BY c.Nombre, pc.CampanaID
    """)).mappings().all()
    return [{"campana_id": int(f["CampanaID"]),
             "nombre": (NOMBRE_EN_PLANIFICADOR.get(int(f["CampanaID"])) or f["Nombre"]
                        or f"Campaña {f['CampanaID']}"),
             "empresa_id": _int_o_none(f["EmpresaID"]),
             "activa": bool(f["Activa"]),
             "con_fuente": int(f["CampanaID"]) in FUENTES} for f in filas]


def campanas_con_fuente(conn: Connection) -> List[int]:
    """Las activas que se pueden recalcular. Es lo que recorren los crons: una
    campaña dada de alta sin fuente fallaría todas las mañanas sin decir nada nuevo."""
    return [c["campana_id"] for c in campanas_del_planificador(conn)
            if c["activa"] and c["con_fuente"]]


# ------------------------------------------------------------------- clima

def hay_clima(conn: Connection, campana_id: int) -> bool:
    return (_tiene_tabla(conn, "planificacion.Clima")
            and _tiene_columna(conn, "planificacion.Campana", "ClimaActivo"))


def clima_de_la_campana(conn: Connection, campana_id: int) -> Optional[dict]:
    """Coordenadas y si el clima está activo. None si falta la migración."""
    if not _tiene_columna(conn, "planificacion.Campana", "ClimaActivo"):
        return None
    f = conn.execute(text("""
        SELECT ClimaLat, ClimaLon, ClimaActivo
        FROM planificacion.Campana WHERE CampanaID = :c
    """), {"c": campana_id}).mappings().fetchone()
    if not f:
        return None
    return {"lat": _float_o_none(f["ClimaLat"]), "lon": _float_o_none(f["ClimaLon"]),
            "activo": bool(f["ClimaActivo"])}


def clima_por_dia(conn: Connection, campana_id: int, desde: date, hasta: date,
                  solo_observado: bool = False) -> Dict[date, dict]:
    """El clima diario del área. `hasta` exclusivo.

    `solo_observado` es para ENTRENAR: el modelo tiene que aprender de lo que
    efectivamente pasó y no del pronóstico meteorológico, o termina aprendiendo
    el error del meteorólogo además del suyo. Para PRONOSTICAR, en cambio, hay
    que usar el pronóstico: es lo único que hay sobre el futuro.
    """
    if not _tiene_tabla(conn, "planificacion.Clima"):
        return {}
    filtro = " AND EsPronostico = 0" if solo_observado else ""
    filas = conn.execute(text(f"""
        SELECT Fecha, TempMax, TempMin, TempMedia, AparenteMax, AparenteMin,
               LluviaMm, VientoKmh, RafagaKmh, HumedadPct, EsPronostico, Origen
        FROM planificacion.Clima
        WHERE CampanaID = :c AND Fecha >= :a AND Fecha < :b{filtro}
        ORDER BY Fecha
    """), {"c": campana_id, "a": desde, "b": hasta}).mappings().all()
    return {f["Fecha"]: {
        "t_max": _float_o_none(f["TempMax"]), "t_min": _float_o_none(f["TempMin"]),
        "t_media": _float_o_none(f["TempMedia"]),
        "t_aparente_max": _float_o_none(f["AparenteMax"]),
        "t_aparente_min": _float_o_none(f["AparenteMin"]),
        "lluvia_mm": _float_o_none(f["LluviaMm"]),
        "viento_kmh": _float_o_none(f["VientoKmh"]),
        "rafaga_kmh": _float_o_none(f["RafagaKmh"]),
        "humedad_pct": _float_o_none(f["HumedadPct"]),
        "es_pronostico": bool(f["EsPronostico"]), "origen": f["Origen"],
    } for f in filas}


def guardar_clima(conn: Connection, campana_id: int, filas: Sequence[dict],
                  origen: str) -> int:
    """Inserta o pisa el clima por día. Devuelve cuántos días quedaron.

    Pisa a propósito: un día que ayer era pronóstico hoy es observación, y tiene
    que quedar con el dato real. Es el mismo criterio con el que se recarga el
    informe IVR.
    """
    if not filas:
        return 0
    conn.execute(text("""
        MERGE planificacion.Clima AS destino
        USING (SELECT :c AS CampanaID, :fecha AS Fecha) AS origen_
            ON destino.CampanaID = origen_.CampanaID AND destino.Fecha = origen_.Fecha
        WHEN MATCHED THEN UPDATE SET
            TempMax = :t_max, TempMin = :t_min, TempMedia = :t_media,
            AparenteMax = :ap_max, AparenteMin = :ap_min, LluviaMm = :lluvia,
            VientoKmh = :viento, RafagaKmh = :rafaga, HumedadPct = :humedad,
            EsPronostico = :es_pron, Origen = :origen,
            ActualizadoEn = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN INSERT
            (CampanaID, Fecha, TempMax, TempMin, TempMedia, AparenteMax,
             AparenteMin, LluviaMm, VientoKmh, RafagaKmh, HumedadPct,
             EsPronostico, Origen)
            VALUES (:c, :fecha, :t_max, :t_min, :t_media, :ap_max, :ap_min,
                    :lluvia, :viento, :rafaga, :humedad, :es_pron, :origen);
    """), [{"c": campana_id, "origen": origen, **f} for f in filas])
    return len(filas)


def nombre_de_campana(conn: Connection, campana_id: int) -> str:
    """Nombre legible de la campaña, para titular lo que se exporta.

    Best-effort: si el catálogo no la tiene, el archivo sale igual con el id. Un
    Excel sin título es incómodo; un Excel que no se puede bajar, un problema.
    """
    try:
        nombre = conn.execute(text(
            "SELECT Nombre FROM [Acme].[calidad].[Campanas] WHERE CampanaID = :c"
        ), {"c": campana_id}).scalar()
        return str(nombre) if nombre else f"Campaña {campana_id}"
    except Exception:
        return f"Campaña {campana_id}"


def _columnas_planilla(conn: Connection) -> str:
    """Las restricciones de la planilla vieja las agrega la migración 2026-09-07b.

    Se pregunta antes de pedirlas para que una base con las migraciones anteriores
    pero sin esta siga funcionando: quedan en NULL, o sea sin restringir, que es
    exactamente lo que valían antes de existir.
    """
    cols = ("MaxAsaSeg, ObjetivoNds2, UmbralSeg2, MinNivelAtencionB"
            if _tiene_columna(conn, "planificacion.Skill", "MaxAsaSeg")
            else "NULL AS MaxAsaSeg, NULL AS ObjetivoNds2, "
                 "NULL AS UmbralSeg2, NULL AS MinNivelAtencionB")
    # La prioridad en el ACD la agrega la migración 2026-09-10c. Sin ella todas
    # las colas se verifican como si esperaran detrás de las demás, que es como
    # venía funcionando.
    cols += (", Prioridad" if _tiene_columna(conn, "planificacion.Skill", "Prioridad")
             else ", CAST(0 AS bit) AS Prioridad")
    return cols


def _int_o_none(v):
    return int(v) if v is not None else None


def _float_o_none(v):
    return float(v) if v is not None else None


def _tiene_columna(conn: Connection, tabla: str, columna: str) -> bool:
    return conn.execute(text("SELECT COL_LENGTH(:t, :c)"),
                        {"t": tabla, "c": columna}).scalar() is not None


def _origen_de_los_pools(conn: Connection, campana_id: int) -> Dict[int, List[int]]:
    """PoolID -> sub-campañas de RRHH que le aportan gente.

    Devuelve vacío si la migración 2026-09-03b todavía no corrió: el planificador
    funciona igual, solo que sin poder comparar contra la malla de RRHH.
    """
    if not _tiene_tabla(conn, "planificacion.PoolOrigen"):
        return {}
    filas = conn.execute(text("""
        SELECT o.PoolID, o.CampanaRRHHID
        FROM planificacion.PoolOrigen o
        JOIN planificacion.Pool p ON p.PoolID = o.PoolID AND p.CampanaID = :c
    """), {"c": campana_id}).fetchall()
    salida: Dict[int, List[int]] = {}
    for pool_id, rrhh_id in filas:
        salida.setdefault(int(pool_id), []).append(int(rrhh_id))
    return salida


# ---------------------------------------------------------------- históricos

def serie_por_skill(conn: Connection, campana_id: int, desde: date,
                    hasta: date) -> Dict[Tuple[datetime, int], Tuple[float, float]]:
    """(momento, skill) -> (llamadas entrantes, TMO en segundos).

    `hasta` es exclusivo. El TMO viene ponderado por llamadas respondidas; los
    intervalos sin respondidas quedan en 0 y el que los use tiene que reemplazar
    ese TMO por el del perfil.
    """
    filas = conn.execute(text(_fuente(campana_id).SERIE),
                         {"desde": desde, "hasta": hasta}).mappings()
    salida: Dict[Tuple[datetime, int], Tuple[float, float]] = {}
    for f in filas:
        # El informe trae filas con `Skill ID` en NULL (llamadas que el reporte no
        # pudo atribuir a una cola). No se pueden dimensionar —no se sabe a qué
        # pool van ni con qué objetivo— así que se descartan acá y no en el SQL,
        # para que `serie_descartada` pueda contarlas y la pantalla avise si son
        # muchas en vez de que desaparezcan en silencio.
        if f["skill_id"] is None:
            continue
        atendidas = float(f["atendidas"] or 0)
        tmo = (float(f["tmo_x_llamadas"] or 0) / atendidas) if atendidas > 0 else 0.0
        salida[(f["momento"], int(f["skill_id"]))] = (float(f["llamadas"] or 0), tmo)
    return salida


def agentes_conectados(conn: Connection, campana_id: int, desde: date,
                       hasta: date) -> Dict[datetime, float]:
    """momento -> operadores conectados. `hasta` exclusivo.

    Son operadores-EQUIVALENTES y no cabezas: el que entró a la mitad del
    intervalo cuenta medio. Ver `FuenteVoltara.CONECTADOS`.
    """
    fuente = _fuente(campana_id)
    if not hasattr(fuente, "CONECTADOS"):
        return {}
    salida: Dict[datetime, float] = {}
    for f in conn.execute(text(fuente.CONECTADOS),
                          {"desde": desde, "hasta": hasta}).mappings():
        if f["momento"] is not None:
            salida[f["momento"]] = round(float(f["agentes"] or 0), 1)
    return salida


def serie_descartada(conn: Connection, campana_id: int, desde: date,
                     hasta: date) -> float:
    """Llamadas del período que el reporte no atribuyó a ningún skill.

    Si esto deja de ser marginal, el pronóstico está perdiendo volumen y hay que
    mirar el reporte de origen antes de confiar en el número."""
    filas = conn.execute(text(_fuente(campana_id).SERIE),
                         {"desde": desde, "hasta": hasta}).mappings()
    return sum(float(f["llamadas"] or 0) for f in filas if f["skill_id"] is None)


def serie_diaria(serie: Dict[Tuple[datetime, int], Tuple[float, float]],
                 skills: Optional[Sequence[int]] = None) -> Dict[date, float]:
    """Total de llamadas por día, para la detección de días atípicos."""
    filtro = set(skills) if skills else None
    diaria: Dict[date, float] = {}
    for (momento, skill_id), (llamadas, _) in serie.items():
        if filtro and skill_id not in filtro:
            continue
        diaria[momento.date()] = diaria.get(momento.date(), 0.0) + llamadas
    return diaria


# País para el calendario de feriados. Voltara y Hidra son argentinas; Gasur es
# uruguaya. La campaña que no figura en `PAIS_POR_CAMPANA` es argentina.
PAIS_FERIADOS = "AR"
PAIS_POR_CAMPANA = {CAMPANA_GASUR: "UY"}


def pais_de_la_campana(campana_id: Optional[int]) -> str:
    return PAIS_POR_CAMPANA.get(campana_id, PAIS_FERIADOS)


def _feriados_de_la_libreria(desde: date, hasta: date, pais: str = PAIS_FERIADOS) -> set:
    """Feriados nacionales según `holidays`. Vacío si la librería no está.

    Import perezoso y falla blanda a propósito: sin la librería el planificador
    sigue andando con la tabla, que es lo que usaba antes. Un `ImportError` en el
    arranque dejaría sin pronóstico a toda la campaña por un calendario.

    Para Uruguay son sólo los 5 feriados NO laborables (la categoría por defecto
    de la librería). Los laborables (Carnaval, Semana de Turismo, 19 de junio,
    etc.) no se comportan como feriado en Gasur: medido 2024-2026 contra el
    mismo día de semana cercano van de x0,2 a x2,6 sin patrón (Turismo 2025 bajó a
    la mitad y la de 2026 subió al doble). Tratarlos como domingo sería peor que
    tratarlos como un día más.
    """
    try:
        import holidays
    except ImportError:
        logger.warning("La librería `holidays` no está instalada: los feriados "
                       "salen sólo de dbo.Feriados y le van a faltar los puentes.")
        return set()
    try:
        anios = range(desde.year, hasta.year + 1)
        cal = holidays.country_holidays(pais, years=anios)
        return {d for d in cal if desde <= d <= hasta}
    except Exception as e:
        logger.warning(f"No se pudo leer el calendario de feriados: {e}")
        return set()


def puentes(desde: date, hasta: date, campana_id: Optional[int] = None) -> List[date]:
    """Los feriados puente ("con fines turísticos") del período, según `holidays`.

    Son parte de `feriados()`; esta lista sólo sirve para que una campaña donde
    el puente NO se comporta como feriado (Hidra: x0,6 de un día hábil) pueda
    tratarlos aparte. Se reconocen por el nombre que les da la librería en
    inglés ("Bridge Public Holiday"), que no depende del idioma del servidor.
    Vacío si la librería no está (y en Uruguay, que no tiene puentes).
    """
    try:
        import holidays
        cal = holidays.country_holidays(pais_de_la_campana(campana_id),
                                        years=range(desde.year, hasta.year + 1),
                                        language="en_US")
    except Exception as e:                                   # noqa: BLE001
        logger.warning(f"No se pudieron leer los puentes: {e}")
        return []
    return sorted(d for d, nombre in cal.items()
                  if desde <= d <= hasta and "bridge" in str(nombre).lower())


def feriados(conn: Connection, desde: date, hasta: date,
             campana_id: Optional[int] = None) -> List[date]:
    """Los feriados del período: la UNIÓN de `holidays` y `dbo.Feriados`.

    POR QUÉ LOS DOS Y NO UNO
    ------------------------
    La librería trae lo que la tabla no: los **puentes turísticos**, que en
    Argentina se fijan por decreto cada año y que nadie se acuerda de cargar a
    mano. Comparado sobre 2026, la tabla tiene 16 días y la librería 19, y los
    tres de diferencia son puentes (23/3, 10/7 y 7/12). No hay ni un día que esté
    en la tabla y no en la librería.

    Y los puentes SÍ son feriados para el teléfono. Llamadas contra el promedio
    del mismo día de semana cercano:

        2025-05-02  0,36     2026-03-23  0,60
        2025-08-15  0,64     2026-07-10  0,58
        2025-11-21  0,94     (feriados de la tabla: 0,15 a 0,69)

    Cuatro de los cinco caen de lleno en el rango de un feriado. Tratándolos como
    un martes cualquiera —que es lo que pasaba— el pronóstico se pasa como 40%.

    La tabla se conserva igual y se SUMA: puede tener días que la librería no
    conoce (un feriado provincial, un corte programado que la operación quiso
    tratar como feriado), y perderlos por cambiar de fuente sería un retroceso.

    OTRO PAÍS. `dbo.Feriados` es el calendario argentino: a una campaña de otro
    país (Gasur, Uruguay) sólo le corresponde la librería con su país.
    """
    pais = pais_de_la_campana(campana_id)
    if pais != PAIS_FERIADOS:
        return sorted(_feriados_de_la_libreria(desde, hasta, pais))
    filas = conn.execute(text(
        "SELECT CAST(Dia AS DATE) AS d FROM dbo.Feriados WHERE Dia >= :a AND Dia <= :b"
    ), {"a": desde, "b": hasta}).fetchall()
    return sorted({f[0] for f in filas} | _feriados_de_la_libreria(desde, hasta))


def perfil_de_tmo(serie: Dict[Tuple[datetime, int], Tuple[float, float]]
                  ) -> Dict[Tuple[int, int, int], float]:
    """(skill, día de semana, minuto del día) -> TMO ponderado.

    El TMO no es constante: en Voltara va de 158s a la noche a 303s en el pico de
    la tarde, o sea casi el doble. Dimensionar con un TMO promedio subestima
    justo en el momento en que más gente hace falta.
    """
    acumulado: Dict[Tuple[int, int, int], Tuple[float, float]] = {}
    for (momento, skill_id), (llamadas, tmo) in serie.items():
        if llamadas <= 0 or tmo <= 0:
            continue
        clave = (skill_id, momento.isoweekday(), momento.hour * 60 + momento.minute)
        peso, total = acumulado.get(clave, (0.0, 0.0))
        acumulado[clave] = (peso + llamadas, total + llamadas * tmo)
    return {k: total / peso for k, (peso, total) in acumulado.items() if peso > 0}


def tmo_para(perfil: Dict[Tuple[int, int, int], float], skill_id: int,
             momento: datetime, respaldo: float = 0.0) -> float:
    """TMO del perfil, con degradación: mismo día de semana y hora, si no
    cualquier día a esa hora, si no el respaldo."""
    minuto = momento.hour * 60 + momento.minute
    exacto = perfil.get((skill_id, momento.isoweekday(), minuto))
    if exacto:
        return exacto
    misma_hora = [v for (s, _, m), v in perfil.items() if s == skill_id and m == minuto]
    if misma_hora:
        return sum(misma_hora) / len(misma_hora)
    del_skill = [v for (s, _, _), v in perfil.items() if s == skill_id]
    return (sum(del_skill) / len(del_skill)) if del_skill else respaldo


# ------------------------------------------------------------- calibraciones

def estimar_paciencia(conn: Connection, campana_id: int, desde: date,
                      hasta: date) -> Dict[str, float]:
    """Paciencia media por skill, en segundos (MLE exponencial censurado).

    theta = abandonos / suma de esperas de todas las llamadas; la media es 1/theta.
    Es el estimador correcto para el modelo que usa Erlang A, aunque la paciencia
    real no sea exponencial: los que abandonan en Voltara esperan 120-250s, pero la
    enorme mayoría nunca llega a esperar tanto, y es esa exposición la que fija la
    tasa.
    """
    salida: Dict[str, float] = {}
    for f in conn.execute(text(_fuente(campana_id).PACIENCIA),
                          {"desde": desde, "hasta": hasta}).mappings():
        abandonos = float(f["abandonos"] or 0)
        espera = float(f["espera_total"] or 0)
        if f["skill"] and abandonos > 0 and espera > 0:
            salida[str(f["skill"])] = round(espera / abandonos, 1)
    return salida


# Intervalos útiles mínimos para dar por medida la disponibilidad de una hora.
# Medido sobre 90 días: de 08 a 22 hay entre 87 y 164 intervalos que aportan,
# pero de 02 a 05 hay entre 9 y 18. Con nueve intervalos no se mide nada; esas
# horas se quedan con lo que estuviera configurado.
MIN_MUESTRAS_POR_HORA = 30


def umbral_real_de(campana_id: int) -> int:
    """Segundos del «NDS real» de la campaña: el umbral con que su fuente cuenta
    las atendidas rápidas. 20 s (Voltara, Hidra) salvo que la fuente diga otro."""
    return int(getattr(FUENTES.get(campana_id), "UMBRAL_REAL_SEG", 20))


def servicio_real(conn: Connection, campana_id: int, skills: Sequence[int],
                  desde: date, hasta: date) -> Dict[datetime, Dict[str, float]]:
    """momento -> lo que realmente pasó con la cola: entrantes, atendidas y
    atendidas dentro del umbral. `hasta` exclusivo.

    Misma consulta que usa la calibración de disponibilidad, porque es la misma
    pregunta: qué nivel de servicio se logró de verdad en ese intervalo. Sirve
    para poner el servicio AL LADO de la dotación, que es la única forma de leer
    una brecha: citar menos de lo que el plan pedía y cumplir el objetivo igual no
    es lo mismo que citar menos y no cumplirlo.

    El umbral de `hasta_20s` es el de la vista de la operación (20s; 10 s en Gasur
    Gas, ver `umbral_real_de`). Ver el comentario de `FuenteVoltara.CALIBRACION`.
    """
    if not skills:
        return {}
    consulta = text(_fuente(campana_id).CALIBRACION).bindparams(
        bindparam("skills", expanding=True))
    salida: Dict[datetime, Dict[str, float]] = {}
    for f in conn.execute(consulta, {"desde": desde, "hasta": hasta,
                                     "skills": list(skills)}).mappings():
        if f["momento"] is None:
            continue
        salida[f["momento"]] = {
            "entrantes": float(f["entrantes"] or 0),
            "atendidas": float(f["atendidas"] or 0),
            "hasta_umbral": float(f["hasta_20s"] or 0),
        }
    return salida


def estimar_disponibilidad(conn: Connection, cfg: pl.CampanaCfg, pool_id: int,
                           desde: date, hasta: date,
                           min_ocupacion: float = 0.55) -> Dict[str, object]:
    """Qué proporción de los presentes está realmente sobre la cola.

    Para cada intervalo busca la dotación efectiva mínima que explicaría el NDS
    que efectivamente se logró, y la divide por la gente que estuvo. El cociente
    es el factor de disponibilidad.

    Solo usa intervalos donde la cola ATÓ: si el NDS real fue ~100%, cualquier
    dotación por encima de cierto piso lo cumple y el intervalo no tiene
    información sobre cuánta gente hacía falta. Sin ese filtro el factor da mayor
    a 1 en el turno noche, que es un artefacto y no un dato.

    El filtro no alcanza: aun con la cola atada queda un 26% de intervalos donde
    el cociente pasa de 1, o sea donde el modelo pide más gente de la que hubo
    para explicar el servicio que igual se logró. Eso NO es disponibilidad mayor
    al 100% —no existe— sino el límite de Erlang con pocos operadores. Por eso el
    factor y los percentiles van topados en 1 y el cociente crudo se informa sólo
    como `sin_explicar`, que es su única lectura honesta.
    """
    skills = [s.skill_id for s in cfg.skills_del_pool(pool_id)]
    if not skills:
        return {"factor": None, "muestras": 0, "motivo": "el pool no tiene skills"}

    fuente = _fuente(cfg.campana_id)
    consulta = text(fuente.CALIBRACION).bindparams(bindparam("skills", expanding=True))
    filas = conn.execute(consulta, {"desde": desde, "hasta": hasta,
                                    "skills": skills}).mappings().all()
    # El umbral con el que la fuente cuenta `hasta_20s`: 20 s salvo Gasur (10 s).
    umbral_real = float(umbral_real_de(cfg.campana_id))

    from app import planificador_erlang as erlang

    ratios: List[float] = []
    por_hora: Dict[int, List[float]] = {}
    for f in filas:
        atendidas = float(f["atendidas"] or 0)
        presentes = int(round(float(f["agentes"] or 0)))
        if atendidas <= 0 or presentes <= 0:
            continue
        tmo = float(f["tmo_x_llamadas"] or 0) / atendidas
        entrantes = float(f["entrantes"] or 0)
        if tmo <= 0 or entrantes <= 0:
            continue
        trafico = erlang.trafico_erlangs(entrantes, tmo, cfg.intervalo_seg)
        if trafico <= 0 or trafico / presentes < min_ocupacion:
            continue          # cola floja: no dice nada sobre la dotación necesaria
        # SOBRE LAS ENTRANTES Y NO SOBRE LAS ATENDIDAS. Las dos lecturas
        # existen, pero la que corresponde es sobre lo que entró: la llamada que
        # se cortó esperando también incumplió el nivel de servicio, y medir sólo
        # contra las atendidas premia justamente al intervalo donde la gente se
        # cansó de esperar. Medido sobre 90 días, 75,01% contra 77,11%. Baja, y
        # es lo correcto —así lo mide también el tablero de la operación—.
        nds_real = float(f["hasta_20s"] or 0) / entrantes
        if nds_real >= 0.995:
            continue          # saturado: cualquier dotación lo cumple

        implicito = None
        for n in range(1, presentes * 3 + 5):
            m = erlang.metricas_a(n, trafico, tmo, umbral_real, cfg.paciencia_seg)
            if m.nds_sobre_entrantes >= nds_real:
                implicito = n
                break
        if implicito:
            ratios.append(implicito / presentes)
            # Además del global, por hora: la disponibilidad de las 3 de la
            # mañana no tiene nada que ver con la de las 11, y una franja de
            # nueve horas promedia dos operaciones distintas.
            por_hora.setdefault(f["momento"].hour, []).append(implicito / presentes)

    if not ratios:
        return {"factor": None, "muestras": 0,
                "motivo": "no hubo intervalos con cola suficiente para estimar"}

    ratios.sort()
    mediana = min(1.0, ratios[len(ratios) // 2])
    # LOS PERCENTILES VAN TOPADOS EN 1, igual que el factor. Un 106% no es una
    # disponibilidad: es un intervalo donde el modelo necesitó MÁS operadores de
    # los que estuvieron logueados para explicar el servicio que igual se logró.
    # Mostrarlo como si fuera disponibilidad invita a leer "está mejor que
    # perfecto", que no significa nada. Lo que sí es información es CUÁNTOS
    # intervalos caen ahí, y eso va aparte, en `sin_explicar`.
    salida = {
        "factor": round(mediana, 3),
        "muestras": len(ratios),
        "p25": round(min(1.0, ratios[len(ratios) // 4]), 3),
        "p75": round(min(1.0, ratios[(3 * len(ratios)) // 4]), 3),
        # DÓNDE EL MODELO NO LLEGA. Erlang supone tiempos de atención
        # exponenciales y llegadas de Poisson dentro del intervalo; con poca gente
        # las dos cosas son pesimistas y la cola real anda mejor de lo que el
        # modelo dice que puede andar. Medido sobre Voltara, 90 días, el reparto es
        # inequívoco y no es ruido parejo:
        #
        #     hora    muestras   no explicados
        #     00-07      ~240        50-70%
        #     09-12       610         6-9%
        #     17-23       657        32-50%
        #
        # En el pico —donde se dimensiona— el modelo explica el 92% de los
        # intervalos. En las horas de 2 a 10 operadores, no: ahí la dotación la
        # fija el mínimo del pool, no el Erlang, y este número lo confirma en vez
        # de esconderlo.
        "sin_explicar": round(sum(1 for r in ratios if r > 1.0) / len(ratios), 3),
    }
    # EL BREAK ESTÁ ADENTRO DE ESTE NÚMERO. El que está en su break está presente
    # y no está sobre la cola, así que el factor medido lo incluye. Lo que se
    # guarda en la tabla es la disponibilidad REAL —sin break—, porque el break
    # se descuenta aparte y así el día que cambie se toca una sola perilla:
    #
    #     factor_real = factor_medido / (1 - break)
    #
    resto = 1.0 - cfg.factor_break()
    salida["break"] = round(cfg.factor_break(), 4)
    # SI NO ENTRA, NO SE PROPONE NADA. Un factor neto mayor que 1 no significa
    # "disponibilidad perfecta": significa que la medición no da para sostener el
    # break declarado —lo que se pierde, medido, es MENOS que el break solo—.
    # Toparlo en 1 y aplicarlo sería sacarle a la dotación el descuento de
    # disponibilidad entero y en silencio, que es la única dirección en la que
    # este número puede hacer daño.
    salida["factor_aplicable"] = (round(mediana / resto, 3)
                                  if mediana / resto <= 1.0 else None)
    if salida["factor_aplicable"] is None:
        salida["conflicto"] = (
            f"La disponibilidad medida ({mediana:.1%}) no alcanza para descontar "
            f"además un break de {cfg.factor_break():.1%}: juntos darían más del "
            f"100%. O el break declarado es más chico que el real, o el modelo "
            f"está explicando el nivel de servicio con menos gente de la que hubo.")
    # Por hora, para poder cargar franjas finas en vez de tres bloques enormes.
    # Sólo las horas con muestra: de madrugada hay 9 intervalos útiles en 90 días
    # y ese número no es una medición, es una anécdota.
    salida["por_hora"] = []
    for h, v in sorted(por_hora.items()):
        if len(v) < MIN_MUESTRAS_POR_HORA:
            continue
        med = min(1.0, sorted(v)[len(v) // 2])
        salida["por_hora"].append({
            "hora": h, "muestras": len(v),
            "factor_medido": round(med, 3),
            # La hora con muchos intervalos sin explicar no es una hora con buena
            # disponibilidad: es una hora donde este método no mide. Se informa
            # para poder descartarla a ojo en vez de aplicarle un 100% que parece
            # una medición y no lo es.
            "sin_explicar": round(sum(1 for r in v if r > 1.0) / len(v), 3),
            # Misma regla que el global: la hora que no entra no propone nada y
            # conserva lo que tuviera configurado.
            "factor_aplicable": round(med / resto, 3) if med / resto <= 1.0 else None,
        })
    return salida


# ------------------------------------------------------------------- payroll
# `dbo.payroll` es la malla de RRHH: qué tenía programado cada operador y qué
# pasó. Es compartida por todas las campañas, así que estas consultas no van en
# la fuente de una campaña: lo específico es qué sub-campañas de RRHH alimentan
# cada pool, y eso vive en `planificacion.PoolOrigen`.

# QUIÉN CUENTA COMO DOTACIÓN TELEFÓNICA
# -------------------------------------
# La nómina de las sub-campañas que alimentan un pool no es la gente que atiende:
# adentro hay supervisores, coordinadores, anfitriones, back office y el puesto
# "Operador Capacitación". Ninguno toma una llamada. Medido sobre Voltara (jun-sep
# 2026), eran 3.113 de 56.576 horas de piso (5,5%), y en la malla FUTURA —que es
# la que se compara contra el plan— 15 personas de más en el pico de 151, o sea
# exactamente el tamaño de un refuerzo.
#
# Qué puesto cuenta lo dice `planificacion.PuestoMalla` y no una lista en el
# código, porque `dbo.puestos` crece: un puesto nuevo tiene que quedar AFUERA
# hasta que alguien lo clasifique, y verse. Misma regla que `CodigoPayroll`.
#
# Sin la migración 2026-09-09e el filtro no se aplica y se cuenta a todos, que es
# el comportamiento de siempre. La alternativa —no contar a nadie— dejaría la
# malla en cero y una brecha inventada en todos los intervalos.
_FILTRO_PUESTO = """
      AND EXISTS (SELECT 1 FROM planificacion.PuestoMalla pm
                  WHERE pm.PuestoID = o.puesto_id AND pm.EnMalla = 1)"""


def _filtro_de_puesto(conn: Connection) -> str:
    return (_FILTRO_PUESTO
            if _tiene_tabla(conn, "planificacion.PuestoMalla") else "")


# Horas por código, para medir el ausentismo real en vez de suponerlo.
#
# EL DÍA EN CURSO NO SE MIDE. `dbo.payroll` da de alta el turno del día con el
# código ABS y lo corrige recién cuando entra la asistencia, así que un día
# abierto dice que faltó todo el mundo. Medido sobre el mismo día:
#
#     2026-09-09 leído el 09 (abierto)   piso   156 h · ausente 724 h  (82%)
#     2026-09-09 leído el 10 (cerrado)   piso   986 h · ausente  34 h  (3,3%)
#
# Por eso `estimar_shrinkage_payroll` recorta `hasta` a hoy: no alcanza con que
# los llamadores se acuerden, porque el error no se ve —da un número plausible,
# sólo que enorme—.
#
# El mismo filtro de puesto que la malla, y no por prolijidad: el shrinkage
# divide la dotación (a_planificar = en_línea / (1 - shrinkage)), así que medirlo
# sobre una población que no es la que se planifica mueve toda la campaña. Con
# toda la nómina daba 13,6% y con los dos puestos que atienden 4,6%, y la
# diferencia son cuatro renglones: supervisores con 3.753 h "ausentes" contra
# 1.737 de piso, coordinadores con 1.107 y cero piso, y el puesto Operador
# Capacitación, que está en capacitación por definición.
#
# OJO CON EL JOIN A `operadores`: NO se filtra por `fecha_hasta IS NULL`.
# `dbo.operadores` es una tabla versionada (una fila por cada vez que a la persona
# le cambió el turno, el equipo o la campaña) y `payroll.id_operadores` apunta a la
# VERSIÓN que estaba vigente ese día. Quedarse con la versión actual descarta la
# historia de todo el que haya tenido algún cambio: medido sobre Voltara en
# jun-ago 2026, se perdían 38.419 de 99.722 horas programadas, casi el 40%. Y el
# sesgo no es aleatorio: sobreviven justo los que no se movieron.
# EL CÓDIGO NO VE TODO LO QUE SE PIERDE. Un turno con código de piso puede
# haberse cumplido a medias —llegó tarde, se fue antes— y el código sigue
# diciendo "P" con las horas programadas enteras. Por eso además de las horas
# programadas se traen las EFECTIVAS, topeadas fila por fila contra lo
# programado: `MIN(trabajadas, programadas)`. El tope es necesario porque
# `horas_trabajadas` incluye extras, y sin él una hora extra de alguien tapa la
# hora que faltó otro.
#
# Medido sobre el pool telefónico, 180 días, sólo operadores: el faltante dentro
# del turno es 1,4% en un día hábil y 4,4% en un domingo. Es justamente donde
# vive la diferencia entre tipos de día que la medición por códigos no encuentra.
#
# Se agrupa por FECHA para poder partir el resultado por tipo de día. Son ~90
# días por ~20 códigos: nada.
_PAYROLL_HORAS = """
    SELECT p.fecha,
           p.codigo,
           ISNULL(c.Clase, 'otro')            AS clase,
           COUNT(*)                           AS filas,
           SUM(ISNULL(p.horas_programadas, 0)) AS horas_programadas,
           SUM(ISNULL(p.horas_trabajadas, 0))  AS horas_trabajadas,
           SUM(CASE WHEN ISNULL(p.horas_trabajadas, 0) < ISNULL(p.horas_programadas, 0)
                    THEN ISNULL(p.horas_trabajadas, 0)
                    ELSE ISNULL(p.horas_programadas, 0) END) AS horas_efectivas
    FROM dbo.payroll p
    JOIN dbo.operadores o ON o.id = p.id_operadores
    LEFT JOIN planificacion.CodigoPayroll c ON c.Codigo = p.codigo
    WHERE p.fecha >= :desde AND p.fecha < :hasta
      AND o.campana_id IN :campanas{puesto}
    GROUP BY p.fecha, p.codigo, ISNULL(c.Clase, 'otro')
"""

# Turnos que cuentan como dotación citada. ALINEADO con la definición de
# `dbo.Tablero_Agentes_Voltara`, que es la que Planificación mira todos los días:
# replicada intervalo por intervalo sobre el 2026-09-09 da 964 contra 964 y un
# pico de 57 contra 57.
#
# TRES COSAS QUE NO SON OBVIAS
# ----------------------------
# 1. SÓLO `payroll_futuro`, nunca `dbo.payroll`. No es que el futuro salga de una
#    tabla y el pasado de otra: `payroll_futuro` tiene 1,2 millones de filas desde
#    2023-11-23, o sea la malla entera, pasada y futura. Leer las dos y unirlas
#    contaba cada turno pasado DOS VECES —245 citados en el pico del 8/9 cuando el
#    máximo posible eran 124—, y obligaba a un corte en "hoy" que ahora no hace
#    falta.
#
# 2. Cuenta al citado que después FALTÓ (`Clase in ('piso','ausente')`). La malla
#    es lo que se PROMETIÓ, y contra eso hay que medir la brecha de planificación;
#    quién estuvo de verdad se ve al lado, en los conectados. El tablero usa la
#    lista literal `codigo IS NULL OR codigo = 'ABS'`; la clase da exactamente lo
#    mismo (964 contra 964) y no se rompe el día que RRHH invente un código de
#    ausencia nuevo, que con la lista literal quedaría afuera en silencio.
#
# 3. `horas_programadas > 0` saca las licencias sin tocar la clasificación: esa
#    gente no estaba citada, así que no es que faltó.
_PAYROLL_TURNOS = """
    SELECT f.id_operadores AS operador,
           DATEADD(second, DATEDIFF(second, '00:00:00', f.inicio),
                   CAST(f.fecha AS datetime)) AS inicio,
           DATEADD(second, DATEDIFF(second, '00:00:00', f.final),
                   CAST(DATEADD(day, CASE WHEN f.final <= f.inicio THEN 1 ELSE 0 END,
                                f.fecha) AS datetime)) AS final
    FROM dbo.payroll_futuro f
    JOIN dbo.operadores o ON o.id = f.id_operadores
    LEFT JOIN planificacion.CodigoPayroll c ON c.Codigo = f.codigo
    WHERE ISNULL(c.Clase, 'piso') IN ('piso', 'ausente')
      AND f.fecha >= :desde AND f.fecha < :hasta
      AND f.horas_programadas > 0
      AND o.campana_id IN :campanas
      AND f.inicio IS NOT NULL AND f.final IS NOT NULL{puesto}
"""

# La misma malla con la fecha de pase a piso de cada persona, para contarla en
# equivalentes por antigüedad. El WHERE tiene que ser IDÉNTICO al de
# `_PAYROLL_TURNOS` (hay test): si no, citados y equivalentes serían dos
# poblaciones y la diferencia no sería el rinde. LEFT JOIN a la nómina: el que no
# tiene fecha cuenta entero, que es lo que se hacía siempre.
_PAYROLL_TURNOS_CON_PISO = """
    SELECT f.id_operadores AS operador,
           DATEADD(second, DATEDIFF(second, '00:00:00', f.inicio),
                   CAST(f.fecha AS datetime)) AS inicio,
           DATEADD(second, DATEDIFF(second, '00:00:00', f.final),
                   CAST(DATEADD(day, CASE WHEN f.final <= f.inicio THEN 1 ELSE 0 END,
                                f.fecha) AS datetime)) AS final,
           n.fecha_piso
    FROM dbo.payroll_futuro f
    JOIN dbo.operadores o ON o.id = f.id_operadores
    LEFT JOIN dbo.nomina n ON n.id = o.legajo_id
    LEFT JOIN planificacion.CodigoPayroll c ON c.Codigo = f.codigo
    WHERE ISNULL(c.Clase, 'piso') IN ('piso', 'ausente')
      AND f.fecha >= :desde AND f.fecha < :hasta
      AND f.horas_programadas > 0
      AND o.campana_id IN :campanas
      AND f.inicio IS NOT NULL AND f.final IS NOT NULL{puesto}
"""


# La misma dotación pero del REGISTRO, no de la malla. Sólo sirve para días
# cerrados y existe porque `payroll_futuro` no es un registro de lo que pasó:
#
#   - No tiene NINGUNA hora extra cargada. Medido sobre el pool telefónico,
#     90 días: `dbo.payroll` informa 1.426,4 h de horas extras sobre las filas de
#     piso y `payroll_futuro` informa CERO. La columna está vacía.
#   - Sí tiene, en cambio, buena parte de las jornadas extendidas metidas en el
#     horario: la ventana (final - inicio) de las dos tablas difiere en 525 h de
#     31.066, o sea que alguien refresca la malla pasada con el horario realizado
#     pero sin poblar `horas_extras`.
#
# Neto, contado intervalo por intervalo, la diferencia es de 1 a 3% en el horario
# de operación (y negativa de 18 a 22). Es chica, pero para la pregunta "cuántos
# hubo" la fuente correcta es el registro y no la malla.
#
# `inicio` y `final` acá ya son datetime completos y consistentes —sobre 80.360
# filas, ninguna tiene `final <= inicio` y el turno que cruza medianoche trae el
# día siguiente en `final`— así que no hace falta el armado que sí necesita
# `payroll_futuro`.
_PAYROLL_TURNOS_REAL = """
    SELECT p.id_operadores AS operador, p.inicio, p.final
    FROM dbo.payroll p
    JOIN dbo.operadores o ON o.id = p.id_operadores
    LEFT JOIN planificacion.CodigoPayroll c ON c.Codigo = p.codigo
    WHERE ISNULL(c.Clase, 'piso') IN ('piso', 'ausente')
      AND p.fecha >= :desde AND p.fecha < :hasta
      AND p.horas_programadas > 0
      AND o.campana_id IN :campanas
      AND p.inicio IS NOT NULL AND p.final IS NOT NULL
      AND p.final > p.inicio{puesto}
"""


def _expandir(sql: str, *nombres: str):
    """Consulta con parámetros de lista (`IN :x`) listos para SQLAlchemy."""
    from sqlalchemy import bindparam
    return text(sql).bindparams(*[bindparam(n, expanding=True) for n in nombres])


def _tiene_tabla(conn: Connection, tabla: str) -> bool:
    """Si la tabla existe. Se pregunta ANTES de consultarla y no se atrapa la
    excepción después: estas funciones corren dentro de la transacción de una
    corrida, y en SQL Server una consulta que falla la deja abortada — el
    `except` salvaría la línea pero haría fracasar todo el recálculo.
    """
    return conn.execute(text("SELECT OBJECT_ID(:t, 'U')"),
                        {"t": tabla}).scalar() is not None


def campanas_rrhh_del_pool(conn: Connection, pool_id: int) -> List[int]:
    """Sub-campañas de RRHH cuya gente atiende este pool.

    Son SÓLO las telefónicas (migración 2026-09-09f), por instrucción de la
    operación: la malla mide a los que están en una campaña telefónica. Con las 14
    sub-campañas anteriores daba 1.400 citados el 2026-09-09, un universo 2,4
    veces más grande que el tablero.

    QUÉ ES "TELEFÓNICA" SE MIDE, NO SE COPIA DEL TABLERO. Desde 2026-09-14 el
    criterio es qué parte de las horas trabajadas de su gente está logueada en la
    línea (informe por agente cruzado con la sub-campaña de ese día). Del 17/08 al
    13/09 la separación es limpia: T1 - Teléfono 89%, Artefactos Dañados 99%, T2T3
    - Teléfono 85%, T1 - Emergencias 81% y T1 - Consumo 100%, contra Gestión SVP
    25%, Digital 20% y el resto debajo de 17%. T1 - Consumo no está en el tablero y
    igual entra (migración 2026-09-14b): son dos personas que atienden todo el día.
    Ver el encabezado de esa migración antes de volver a alinear con el tablero.

    NO REVERTIR CITANDO EL COMENTARIO VIEJO. Decía que filtrar por las
    telefónicas daba "42 planificados en el pico contra 57 realmente atendiendo",
    pero ese 42 salió de `dotacion_planificada` cuando todavía contaba cada turno
    pasado dos veces (ver `_PAYROLL_TURNOS`), así que no es reproducible.

    T1 - CONSUMO SALIÓ OTRA VEZ el 2026-09-16 (migración 2026-09-16b), y no por el
    tablero: son 2 operadores de COMERCIAL-CONSUMO, una cola sin llamadas en
    nuestros informes. Logueados todo el día, sí, pero atendieron 106 llamadas de
    nuestras colas en 30 días. Ver `subcampanas_del_pool` ('dedicada').

    Lo que sí es cierto es que gente de Digital y BackOffice también atiende: en
    el pico del 2026-09-09 había 71 conectados de sub-campañas telefónicas contra
    50 citados. Esas manos siguen contando donde corresponde —el factor de
    disponibilidad se mide sobre el informe de skills, que cuenta a quien atendió
    sin mirar su sub-campaña— pero ya no como dotación citada.

    Devuelve vacío mientras no esté aplicada la migración 2026-09-03b: el
    planificador funciona igual, sólo que sin poder compararse con la malla.
    """
    if not _tiene_tabla(conn, "planificacion.PoolOrigen"):
        return []
    filas = conn.execute(text(
        "SELECT CampanaRRHHID FROM planificacion.PoolOrigen WHERE PoolID = :p"
    ), {"p": pool_id}).fetchall()
    return [int(f[0]) for f in filas]


CLASES_SUBCAMPANA = ("telefonica_parcial", "digital", "dedicada")


def subcampanas_del_pool(conn: Connection, pool_id: int, clase: str) -> List[int]:
    """Sub-campañas de RRHH que no son del pool ni la palanca, por clase
    (migración 2026-09-16, respuesta de la operación):

      - 'telefonica_parcial': atienden la línea algunos días sin saberse cuáles
        (Contingencia, Gestión SVP, BU - Anfitrión). Cuentan como telefónicas
        sólo en las medias horas de su turno en que estuvieron en la línea.
      - 'digital': digitales que NO se pasan al teléfono (BackOffice, RRSS…). Su
        gente conectada suma a «+ Digital», pero no a la palanca.
      - 'dedicada': teléfono, pero de una cola que no se planifica (T1 - Consumo,
        COMERCIAL-CONSUMO, migración 2026-09-16b). No cuenta en ningún lado; sus
        conectados se informan aparte.

    Vacío sin la migración: esas sub-campañas siguen como «otras».
    """
    if clase not in CLASES_SUBCAMPANA:
        raise ValueError(f"Clase de sub-campaña desconocida: {clase}")
    if not _tiene_tabla(conn, "planificacion.PoolSubCampana"):
        return []
    filas = conn.execute(text(
        "SELECT CampanaRRHHID FROM planificacion.PoolSubCampana "
        "WHERE PoolID = :p AND Clase = :k"
    ), {"p": pool_id, "k": clase}).fetchall()
    return [int(f[0]) for f in filas]


def campanas_refuerzo_del_pool(conn: Connection, pool_id: int) -> List[int]:
    """Sub-campañas de RRHH cuya gente se puede PASAR a la línea del pool cuando
    no se llega con la malla (migración 2026-09-14).

    No son parte del pool: no cuentan como citados ni se les mide el shrinkage.
    Son la palanca. En Voltara es Digital, que es back office sin SLA; la operación
    dijo el 2026-09-14 que pasan todas menos Agrupadas - Digital y Ajustes -
    Lecturas.

    Vacío mientras la migración no esté aplicada: el plan sale igual, sin la capa
    de refuerzo.
    """
    if not _tiene_tabla(conn, "planificacion.PoolRefuerzo"):
        return []
    filas = conn.execute(text(
        "SELECT CampanaRRHHID FROM planificacion.PoolRefuerzo WHERE PoolID = :p"
    ), {"p": pool_id}).fetchall()
    return [int(f[0]) for f in filas]


def estimar_shrinkage_payroll(conn: Connection, pool_ids: Sequence[int],
                              desde: date, hasta: date,
                              feriados: Optional[Sequence[date]] = None
                              ) -> Dict[str, object]:
    """Shrinkage de nómina medido: qué proporción de las horas programadas no
    llega a la cola.

    DEVUELVE DOS NÚMEROS Y NO UNO, porque contestan preguntas distintas:

      - `shrinkage` —el que se APLICA— va sobre el universo de la malla (horas de
        clase 'piso' y 'ausente'), que es exactamente la población que
        `dotacion_planificada` cuenta como "citados por RRHH". Sus causas son el
        **ausentismo** (programado y no vino) y el faltante **dentro del turno**
        (llegó tarde, se fue antes).
      - `shrinkage_nomina` va sobre TODAS las horas programadas y suma la
        **capacitación** (vino, cobra, pero no atiende). Sirve para dimensionar la
        nómina, no el intervalo.

    La distinción no es cosmética: el que está en capacitación no figura en la
    malla, así que descontárselo a la malla es contarlo dos veces. Ver el
    comentario de `universo` más abajo, con las horas medidas.

    El de adentro del turno no lo ve ningún código: la fila sigue diciendo "P"
    con las horas programadas enteras. Medido sobre el pool telefónico, 90 días:
    el shrinkage pasa de 6,7% a 8,2% cuando se lo cuenta.

    OJO CON CUÁNTO DESCUENTA ESTE NÚMERO DE VERDAD. Medido contra el tablero
    —mismos puestos, mismas sub-campañas, 90 días— los operadores-equivalentes
    logueados dan igual que los citados en todo el horario de operación:

        hora   citados   asistencia   logueados/piso   eq/persona   eq/citados
         09      4.501      92,0%          1,21           0,89         0,99
         11      6.123      92,2%          1,19           0,93         1,02
         13      5.616      93,4%          1,23           0,85         0,97
         16      4.192      95,5%          1,16           0,91         1,01

    O sea: el 7% que falta por ausentismo lo devuelve el solape de los bordes de
    turno y las horas extras, y entre citados y logueados no hay pérdida neta.
    Este descuento queda igual —es prudencia y está medido sobre payroll, que es
    la fuente que RRHH firma— pero conviene saber que la realidad no lo pide.

    Las licencias (vacaciones, licencias largas) NO cuentan: esa gente no está
    programada, así que no infla el denominador. Contarlas sería mezclar "no vino
    el que tenía que venir" con "no estaba previsto que viniera".

    `feriados` habilita el desglose por tipo de día, que es lo que contesta si
    conviene un número por tipo o alcanza con uno solo. Ver `por_tipo_de_dia`.
    """
    campanas: List[int] = []
    for pool_id in pool_ids:
        campanas.extend(campanas_rrhh_del_pool(conn, pool_id))
    campanas = sorted(set(campanas))
    if not campanas:
        return {"shrinkage": None, "motivo": "el pool no tiene sub-campañas de RRHH asociadas"}

    # El día en curso miente y miente fuerte: ver el comentario de
    # `_PAYROLL_HORAS`. Se recorta acá y no en el llamador porque el error no se
    # nota —el número que sale es plausible, sólo que multiplicado por veinte—.
    hasta = min(hasta, date.today())
    if hasta <= desde:
        return {"shrinkage": None,
                "motivo": "todavía no hay ningún día cerrado en el período"}

    filtro = _filtro_de_puesto(conn)
    filas = conn.execute(_expandir(_PAYROLL_HORAS.format(puesto=filtro), "campanas"),
                         {"desde": desde, "hasta": hasta, "campanas": campanas}
                         ).mappings().all()
    if not filas:
        return {"shrinkage": None, "motivo": "no hay filas de payroll en el período"}

    horas: Dict[str, float] = {}
    sin_clasificar: List[str] = []
    efectivas = 0.0
    por_dia: Dict[date, Dict[str, float]] = {}
    for f in filas:
        clase = f["clase"]
        hp = float(f["horas_programadas"] or 0)
        horas[clase] = horas.get(clase, 0.0) + hp
        if clase == "otro" and hp > 0:
            sin_clasificar.append(f["codigo"])
        if clase == "piso":
            efectivas += float(f["horas_efectivas"] or 0)
        dia = f["fecha"]
        acum = por_dia.setdefault(dia, {"programadas": 0.0, "efectivas": 0.0,
                                        "piso": 0.0, "ausente": 0.0,
                                        "capacitacion": 0.0, "otro": 0.0})
        if clase in ("piso", "ausente", "capacitacion", "otro"):
            acum["programadas"] += hp
            acum[clase] += hp
        if clase == "piso":
            acum["efectivas"] += float(f["horas_efectivas"] or 0)

    piso = horas.get("piso", 0.0)
    ausente = horas.get("ausente", 0.0)
    capacitacion = horas.get("capacitacion", 0.0)
    otro = horas.get("otro", 0.0)
    programadas = piso + ausente + capacitacion + otro
    if programadas <= 0:
        return {"shrinkage": None, "motivo": "no hay horas programadas en el período"}
    # Lo que se perdió sin que ningún código lo dijera.
    dentro_del_turno = max(piso - efectivas, 0.0)
    # EL UNIVERSO DE LA MALLA, que es SOBRE QUIÉN se aplica el descuento.
    # `dotacion_planificada` —los "citados por RRHH" de la pantalla— cuenta
    # únicamente las filas de clase 'piso' y 'ausente'. El que ese día está en
    # capacitación NO figura ahí. Descontarle entonces a esa malla un shrinkage
    # que incluye la capacitación es contarla dos veces, y no es chico: medido
    # sobre el pool telefónico de Voltara, 90 días,
    #
    #     horas piso         29.000,3   (efectivas 28.516,6)
    #     horas ausente       2.065,5
    #     horas capacitación  2.912,0   <- fuera de la malla
    #
    #     sobre las programadas (las tres)   16,1%  <- lo que se aplicaba
    #     sobre el universo de la malla       8,2%  <- lo que corresponde
    #
    # O sea que el plan pedía un 8,6% más de gente de la que hace falta, en todos
    # los intervalos. La capacitación sigue midiéndose y sigue a la vista, pero
    # como número de NÓMINA —cuánta gente de más hay que tener para sostener esta
    # malla— y no como descuento del intervalo.
    universo = piso + ausente

    return {
        # Sobre qué población se midió. Un shrinkage de 4,6% y uno de 13,6% son
        # los dos correctos y miden cosas distintas; sin este dato al lado, el
        # número de la pantalla no se puede interpretar.
        "solo_puestos_de_malla": bool(filtro),
        # Hasta dónde se midió de verdad. Si alguien pidió hasta hoy y se le
        # devuelve hasta ayer, tiene que poder verlo.
        "hasta_medido": hasta,
        # EL QUE SE APLICA: sobre el universo de la malla (piso + ausente), que
        # es la población que `dotacion_planificada` cuenta. Sus componentes
        # suman el total por construcción:
        #   universo - efectivas = ausente + (piso - efectivas)
        "shrinkage": round((ausente + dentro_del_turno) / universo, 3)
                     if universo > 0 else None,
        "ausentismo": round(ausente / universo, 3) if universo > 0 else None,
        "dentro_del_turno": round(dentro_del_turno / universo, 3)
                            if universo > 0 else None,
        "horas_universo_malla": round(universo, 1),
        # EL DE NÓMINA: sobre TODAS las horas programadas, capacitación incluida.
        # No se aplica al intervalo —ver el comentario de `universo`— pero es el
        # que contesta "cuánta gente de más tengo que tener en la nómina".
        "shrinkage_nomina": round(
            (ausente + capacitacion + otro + dentro_del_turno) / programadas, 3),
        "capacitacion": round(capacitacion / programadas, 3),
        "sin_clasificar": round(otro / programadas, 3),
        "horas_programadas": round(programadas, 1),
        "horas_en_piso": round(piso, 1),
        "horas_efectivas": round(efectivas, 1),
        "codigos_sin_clasificar": sorted(set(sin_clasificar)),
        "campanas_rrhh": campanas,
        "por_tipo_de_dia": _shrinkage_por_tipo(por_dia, feriados),
    }


def _shrinkage_por_tipo(por_dia: Dict[date, Dict[str, float]],
                        feriados: Optional[Sequence[date]]) -> List[dict]:
    """El mismo shrinkage, partido por tipo de día, con su dispersión.

    Va la DISPERSIÓN y no sólo el promedio porque es lo único que permite decidir
    si conviene un número por tipo de día o alcanza con uno solo. Medido sobre el
    pool telefónico y 180 días, el promedio diario da 8,6% en hábiles y 8,7% en
    domingos, con un desvío de 4,9 y 8,0 puntos: la diferencia entre tipos es una
    décima contra un error estándar de punto y medio, o sea nada. El feriado sí
    es otra cosa —2,1% contra 8,6%, ocho errores estándar— y tiene explicación:
    el que no trabaja un feriado se carga con licencia y CERO horas programadas,
    así que la malla del feriado es de voluntarios y se cumple casi entera.

    Va sobre el universo de la malla, igual que el número general: ver
    `estimar_shrinkage_payroll`.
    """
    fer = {d for d in (feriados or ())}
    grupos: Dict[str, List[float]] = {}
    suma: Dict[str, Dict[str, float]] = {}
    for dia, v in por_dia.items():
        if v["programadas"] <= 0:
            continue
        if dia in fer:
            tipo = "feriado"
        elif dia.weekday() == 6:
            tipo = "domingo"
        elif dia.weekday() == 5:
            tipo = "sabado"
        else:
            tipo = "habil"
        universo_dia = v["piso"] + v["ausente"]
        if universo_dia <= 0:
            continue
        grupos.setdefault(tipo, []).append(1 - v["efectivas"] / universo_dia)
        acum = suma.setdefault(tipo, {k: 0.0 for k in
                                      ("programadas", "piso", "efectivas",
                                       "ausente", "capacitacion", "otro")})
        for k in acum:
            acum[k] += v[k]

    salida = []
    for tipo in ("habil", "sabado", "domingo", "feriado"):
        valores = grupos.get(tipo)
        if not valores:
            continue
        n = len(valores)
        media = sum(valores) / n
        desvio = (sum((x - media) ** 2 for x in valores) / (n - 1)) ** 0.5 if n > 1 else 0.0
        a = suma[tipo]
        prog = a["programadas"]
        universo = a["piso"] + a["ausente"]
        dentro = max(a["piso"] - a["efectivas"], 0.0)
        if universo <= 0:
            continue
        salida.append({
            "tipo": tipo, "dias": n,
            "horas_programadas": round(prog, 1),
            "horas_universo_malla": round(universo, 1),
            # Ponderado por horas y sobre el universo de la malla, igual que el
            # número general: ver el comentario de `universo` en
            # `estimar_shrinkage_payroll`. Los componentes de acá suman el
            # `shrinkage` de acá.
            "shrinkage": round((a["ausente"] + dentro) / universo, 3),
            "shrinkage_nomina": round(
                (a["ausente"] + a["capacitacion"] + a["otro"] + dentro) / prog, 3),
            "ausentismo": round(a["ausente"] / universo, 3),
            # En sábado, domingo y feriado esto tiene que dar CERO: no se dicta
            # capacitación esos días. Si alguna vez no da cero, no es un matiz
            # del promedio —es una fila mal cargada— y por eso se informa aparte
            # en vez de diluirse adentro del total.
            "capacitacion": round(a["capacitacion"] / prog, 3),
            "dentro_del_turno": round(dentro / universo, 3),
            # El promedio DÍA A DÍA y su dispersión van aparte del ponderado:
            # son los que dicen si la diferencia contra otro tipo de día
            # significa algo o entra en el ruido de un día cualquiera.
            "promedio_diario": round(media, 3),
            "desvio": round(desvio, 3),
            "error_estandar": round(desvio / (n ** 0.5), 3) if n else None,
        })
    return salida


def dotacion_planificada(conn: Connection, pool_id: int, desde: date, hasta: date,
                         intervalo_min: int = 30) -> Dict[datetime, int]:
    """Operadores citados en cada intervalo, según la malla de RRHH.

    El conteo se hace en Python y no en SQL a propósito: cruzar la grilla de
    intervalos contra payroll en la base es un producto cartesiano que crece con
    el horizonte, y acá son unos pocos miles de turnos que entran de sobra en
    memoria.

    Sale toda de `payroll_futuro`, que tiene la malla entera —pasada y futura— y
    es la misma definición que usa el tablero de la operación. Ver el comentario
    de `_PAYROLL_TURNOS`.
    """
    return _contar_turnos(conn, _PAYROLL_TURNOS, pool_id, desde, hasta, intervalo_min)


def dotacion_equivalente(conn: Connection, pool_id: int, desde: date, hasta: date,
                         curva: Dict[int, float],
                         intervalo_min: int = 30) -> Dict[datetime, float]:
    """Los citados de `dotacion_planificada` pesados por su antigüedad.

    Misma malla, mismas personas; cada una suma el factor de su tramo ese día
    (`planificador_antiguedad`). Vacío sin curva: no hay nada que pesar.
    """
    campanas = campanas_rrhh_del_pool(conn, pool_id)
    if not campanas or not curva:
        return {}
    turnos = conn.execute(
        _expandir(_PAYROLL_TURNOS_CON_PISO.format(puesto=_filtro_de_puesto(conn)),
                  "campanas"),
        {"desde": desde, "hasta": hasta, "campanas": campanas}).fetchall()
    return pant.contar_equivalentes(turnos, curva, intervalo_min)


def dotacion_real(conn: Connection, pool_id: int, desde: date, hasta: date,
                  intervalo_min: int = 30) -> Dict[datetime, int]:
    """Operadores que REALMENTE tenían turno en cada intervalo, del registro.

    Misma definición y misma población que `dotacion_planificada`, pero leída de
    `dbo.payroll` en vez de la malla. Sólo tiene sentido para días cerrados: es la
    respuesta a "cuántos hubo", no a "cuántos se prometieron". Ver el comentario de
    `_PAYROLL_TURNOS_REAL`, con la diferencia medida entre las dos fuentes.
    """
    return _contar_turnos(conn, _PAYROLL_TURNOS_REAL, pool_id, desde, hasta,
                          intervalo_min)


def refuerzo_planificado(conn: Connection, pool_id: int, desde: date, hasta: date,
                         intervalo_min: int = 30) -> Dict[datetime, int]:
    """Gente del REFUERZO (Digital) con turno en cada intervalo, según la malla.

    La misma definición que `dotacion_planificada` —mismos puestos, mismas
    clases de código, personas y no filas—, sólo que sobre las sub-campañas de
    `campanas_refuerzo_del_pool`. Así "citados + refuerzo" suma gente contada igual.
    """
    return _contar_turnos_de(conn, _PAYROLL_TURNOS,
                             campanas_refuerzo_del_pool(conn, pool_id),
                             desde, hasta, intervalo_min)


def refuerzo_real(conn: Connection, pool_id: int, desde: date, hasta: date,
                  intervalo_min: int = 30) -> Dict[datetime, int]:
    """Lo mismo que `refuerzo_planificado` pero del registro del día cerrado."""
    return _contar_turnos_de(conn, _PAYROLL_TURNOS_REAL,
                             campanas_refuerzo_del_pool(conn, pool_id),
                             desde, hasta, intervalo_min)


def dotacion_parcial_real(conn: Connection, campana_id: int, pool_id: int,
                          desde: date, hasta: date) -> Dict[datetime, int]:
    """Personas de las sub-campañas 'telefonica_parcial' que tenían turno Y
    estuvieron logueadas en la línea, por intervalo. Se suma a `dotacion_real`
    para «tenían turno» en los días cerrados. `hasta` exclusivo.

    Vacío sin la migración 2026-09-16, sin sub-campañas de esa clase o si la
    fuente no tiene el informe por agente."""
    campanas = subcampanas_del_pool(conn, pool_id, "telefonica_parcial")
    fuente = _fuente(campana_id)
    if not campanas or not hasattr(fuente, "TURNOS_PARCIALES_EN_LINEA"):
        return {}
    filas = conn.execute(
        _expandir(fuente.TURNOS_PARCIALES_EN_LINEA.format(puesto=_filtro_de_puesto(conn)),
                  "campanas"),
        {"desde": desde, "hasta": hasta, "campanas": campanas}).mappings()
    return {f["momento"]: int(f["personas"]) for f in filas if f["momento"] is not None}


def conectados_por_origen(conn: Connection, campana_id: int,
                          pool_ids: Sequence[int], desde: date,
                          hasta: date, con_en_linea: bool = False
                          ) -> Dict[datetime, Dict[str, float]]:
    """momento -> {"pool", "refuerzo", "digital", "otras"} en operadores-equivalentes.

    "pool" suma a las sub-campañas 'telefonica_parcial': un conectado de esas
    sub-campañas está, por definición, atendiendo el teléfono en ese intervalo.
    "digital" son las digitales que no son palanca ('digital' en PoolSubCampana) y
    "dedicada" la gente de colas que no se planifican ('dedicada').

    Con `con_en_linea` suma además "pool_en_linea", "refuerzo_en_linea" y
    "otras_en_linea": los mismos conectados sin el tiempo en pausa (break, pausa
    activa, etc.; ver `FuenteVoltara.CONECTADOS_POR_CAMPANA`). Van aparte y no
    reemplazan a los logueados porque la presencia contra los turnos se mide
    logueado —el break ya está en la disponibilidad— y restarlo ahí lo contaría dos
    veces.

    Parte los conectados de `agentes_conectados` según de dónde es cada persona
    ese día: de las sub-campañas del pool, de las del refuerzo, o de otras. Es lo
    que explica que haya más conectados que gente con turno: medido el 2026-09-09
    a las 11:00, de 62,1 conectados 53,9 eran del pool y 6,0 de Digital.

    Vacío si la fuente no tiene la consulta. `hasta` exclusivo.
    """
    fuente = _fuente(campana_id)
    if not hasattr(fuente, "CONECTADOS_POR_CAMPANA"):
        return {}
    del_pool: set = set()
    de_refuerzo: set = set()
    digitales: set = set()
    dedicadas: set = set()
    for pid in pool_ids:
        del_pool.update(campanas_rrhh_del_pool(conn, pid))
        del_pool.update(subcampanas_del_pool(conn, pid, "telefonica_parcial"))
        de_refuerzo.update(campanas_refuerzo_del_pool(conn, pid))
        digitales.update(subcampanas_del_pool(conn, pid, "digital"))
        dedicadas.update(subcampanas_del_pool(conn, pid, "dedicada"))
    grupos = ("pool", "refuerzo", "digital", "dedicada", "otras")
    vacia = {g: 0.0 for g in grupos}
    if con_en_linea:
        vacia.update({f"{g}_en_linea": 0.0 for g in grupos})
    salida: Dict[datetime, Dict[str, float]] = {}
    for f in conn.execute(text(fuente.CONECTADOS_POR_CAMPANA),
                          {"desde": desde, "hasta": hasta}).mappings():
        if f["momento"] is None:
            continue
        cid = f["campana_id"]
        # Si alguna vez la misma sub-campaña estuviera en los dos lados, cuenta
        # como del pool: es la gente que ya se citó.
        grupo = ("pool" if cid in del_pool
                 else "refuerzo" if cid in de_refuerzo
                 else "digital" if cid in digitales
                 else "dedicada" if cid in dedicadas else "otras")
        fila = salida.setdefault(f["momento"], dict(vacia))
        fila[grupo] += float(f["agentes"] or 0)
        if con_en_linea:
            fila[f"{grupo}_en_linea"] += float(f.get("agentes_en_linea") or 0)
    return {m: {k: round(v, 2) for k, v in g.items()} for m, g in salida.items()}


def _contar_turnos(conn: Connection, consulta: str, pool_id: int, desde: date,
                   hasta: date, intervalo_min: int) -> Dict[datetime, int]:
    return _contar_turnos_de(conn, consulta, campanas_rrhh_del_pool(conn, pool_id),
                             desde, hasta, intervalo_min)


def _contar_turnos_de(conn: Connection, consulta: str, campanas: Sequence[int],
                      desde: date, hasta: date,
                      intervalo_min: int) -> Dict[datetime, int]:
    if not campanas:
        return {}

    turnos = conn.execute(
        _expandir(consulta.format(puesto=_filtro_de_puesto(conn)), "campanas"),
        {"desde": desde, "hasta": hasta, "campanas": campanas}).fetchall()
    if not turnos:
        return {}

    # SE CUENTAN PERSONAS, NO FILAS. Un mismo operador puede tener varias filas
    # el mismo día y que se pisen, y contarlas lo pondría dos veces en el mismo
    # intervalo. Pasa por dos caminos:
    #
    #   - Horas extras cargadas como fila APARTE en el registro. En `dbo.payroll`
    #     la extra viene a veces extendiendo el turno (09:00-17:00, 8 h = 6 + 2) y a
    #     veces como otra fila (08:00-09:00 con 1 h de extra). Medido sobre el pool
    #     telefónico, 392 operador-día con varias filas en 90 días.
    #   - Cargas duplicadas. El operador 107085 tiene el 14/06/2026 el mismo turno
    #     de 18:00 a 06:00 cargado 16 veces en el registro y 25 en la malla: contado
    #     por filas eran 16 y 25 personas en cada media hora de esa noche.
    #
    # Hoy, entre el 25/08 y el 14/10, filas y personas dan igual en todos los
    # intervalos (0% de diferencia), así que esto no mueve ningún número vigente:
    # está para que la próxima carga así no infle "tenían turno" sin que se note.
    paso = timedelta(minutes=intervalo_min)
    presentes: Dict[datetime, set] = {}
    for operador, inicio, final in turnos:
        if not inicio or not final or final <= inicio:
            continue
        # Al primer intervalo que toca el turno, redondeando hacia abajo.
        momento = inicio.replace(
            minute=(inicio.minute // intervalo_min) * intervalo_min,
            second=0, microsecond=0)
        while momento < final:
            presentes.setdefault(momento, set()).add(operador)
            momento += paso
    return {m: len(ops) for m, ops in presentes.items()}


# ------------------------------------------------------------------ paciencia

def estimar_paciencia_km(conn: Connection, campana_id: int, desde: date, hasta: date,
                         horizonte_seg: int = 60, paso_seg: int = 5,
                         tope_seg: int = 600) -> Dict[str, dict]:
    """Paciencia por skill estimada con Kaplan-Meier, sin suponer forma.

    POR QUÉ NO ALCANZA EL MLE EXPONENCIAL
    -------------------------------------
    El MLE global (abandonos / suma de esperas) da un número correcto *para el
    modelo exponencial*, pero la paciencia real no es exponencial y ese estimador
    lo arrastra la cola larga: en Emergencias daba 787s cuando el ajuste al rango
    donde la gente realmente espera da 1.126s. Como Erlang A sí necesita una
    exponencial, lo que corresponde es elegir la que reproduce el abandono
    observado EN EL RANGO QUE IMPORTA, no la que mejor ajusta una cola de diez
    minutos que casi nadie recorre.

    Kaplan-Meier trata correctamente la censura: al que fue atendido a los 15
    segundos no sabemos cuánto más habría aguantado, solo que aguantó 15.

    `horizonte_seg` es el rango de ajuste, y por defecto son 60 segundos: tres
    veces el umbral contractual de 20s, o sea la zona donde de verdad se decide el
    nivel de servicio. Devuelve además la curva completa, que es lo que hay que
    mirar para discutir el número: "a los 60 segundos sigue esperando el 94,8%" se
    entiende, "la paciencia media es de 1.126 segundos" no.
    """
    filas = conn.execute(text(_fuente(campana_id).PACIENCIA_CURVA),
                         {"desde": desde, "hasta": hasta,
                          "paso": paso_seg, "tope": tope_seg}).mappings().all()

    por_skill: Dict[str, List[tuple]] = {}
    for f in filas:
        por_skill.setdefault(str(f["skill"]), []).append(
            (int(f["bucket"]), int(f["abandonos"] or 0), int(f["atendidas"] or 0)))

    salida: Dict[str, dict] = {}
    for skill, tramos in por_skill.items():
        tramos.sort()
        total = sum(a + c for _, a, c in tramos)
        if total < 500:
            continue        # muestra chica: el número no se sostiene

        en_riesgo = total
        supervivencia = 1.0
        curva: Dict[int, float] = {}
        abandonos = 0
        for bucket, a, c in tramos:
            if en_riesgo > 0 and a > 0:
                supervivencia *= (1 - a / en_riesgo)
            curva[bucket] = supervivencia
            en_riesgo -= (a + c)
            abandonos += a

        s_horizonte = _supervivencia_en(curva, horizonte_seg)
        # La exponencial que pasa por ese punto: S(t) = exp(-t/paciencia).
        if 0 < s_horizonte < 1:
            paciencia = horizonte_seg / -math.log(s_horizonte)
        else:
            paciencia = None

        salida[skill] = {
            "paciencia_seg": round(paciencia) if paciencia else None,
            "horizonte_seg": horizonte_seg,
            "llamadas": total,
            "abandonos": abandonos,
            "abandono_observado": round(abandonos / total, 4) if total else None,
            "curva": {str(t): round(_supervivencia_en(curva, t), 4)
                      for t in (10, 20, 30, 60, 120, 180, 300)},
        }
    return salida


def _supervivencia_en(curva: Dict[int, float], t: int) -> float:
    """S(t) leyendo el último tramo que ya terminó antes de t."""
    anteriores = [b for b in curva if b <= t]
    return curva[max(anteriores)] if anteriores else 1.0


# -------------------------------------------------------- eventos y ajustes

def eventos(conn: Connection, campana_id: int, desde: date,
            hasta: date) -> List[dict]:
    filas = conn.execute(text("""
        SELECT EventoID, CampanaID, Desde, Hasta, Tipo, Descripcion, Factor,
               Origen, ExcluirDeEntrenamiento, Confirmado
        FROM planificacion.Evento
        WHERE (CampanaID = :c OR CampanaID IS NULL)
          AND Hasta >= :a AND Desde < :b
        ORDER BY Desde DESC
    """), {"c": campana_id, "a": desde, "b": hasta}).mappings().all()
    return [dict(f) for f in filas]


def dias_a_excluir(conn: Connection, campana_id: int, desde: date,
                   hasta: date) -> List[date]:
    """Días marcados como atípicos que no tienen que entrenar el perfil.

    `Hasta` es EXCLUSIVO, como lo escribe el detector (un día atípico va de d a
    d+1) y como lo lee al buscar los que ya están. Hasta el 2026-09-24 se leía
    inclusivo y cada atípico sacaba también el día siguiente del entrenamiento: en
    Gasur, el día después de cada paro, que es un día normal.
    """
    dias: List[date] = []
    for e in eventos(conn, campana_id, desde, hasta):
        if not e["ExcluirDeEntrenamiento"]:
            continue
        dia = e["Desde"].date()
        while datetime.combine(dia, datetime.min.time()) < e["Hasta"]:
            dias.append(dia)
            dia += timedelta(days=1)
    return dias


def ajustes_vigentes(conn: Connection, campana_id: int, desde: date,
                     hasta: date) -> List[dict]:
    filas = conn.execute(text("""
        SELECT AjusteID, SkillID, Desde, Hasta, Factor, Motivo
        FROM planificacion.Ajuste
        WHERE CampanaID = :c AND Activo = 1 AND Hasta >= :a AND Desde < :b
        ORDER BY Desde
    """), {"c": campana_id, "a": desde, "b": hasta}).mappings().all()
    return [dict(f) for f in filas]


def factor_de_ajuste(ajustes: Sequence[dict], momento: datetime,
                     skill_id: int) -> float:
    """Producto de los ajustes manuales que caen sobre ese intervalo y skill.

    Se multiplican y no se pisan: dos ajustes que se solapan (uno de campaña por
    la facturación y otro de skill por una campaña puntual) son dos efectos
    distintos y los dos valen.
    """
    factor = 1.0
    for a in ajustes:
        if a["SkillID"] is not None and a["SkillID"] != skill_id:
            continue
        if a["Desde"] <= momento < a["Hasta"]:
            factor *= float(a["Factor"])
    return factor


# ------------------------------------------------------------------ corridas

def crear_corrida(conn: Connection, campana_id: int, horizonte: str, desde: date,
                  hasta: date, modelo: str, usuario: Optional[int]) -> int:
    fila = conn.execute(text("""
        INSERT INTO planificacion.Corrida
            (CampanaID, Horizonte, Desde, Hasta, Modelo, Estado, CreadoPor)
        OUTPUT INSERTED.CorridaID
        VALUES (:c, :h, :d, :ha, :m, 'EN_CURSO', :u)
    """), {"c": campana_id, "h": horizonte, "d": desde, "ha": hasta,
           "m": modelo, "u": usuario}).scalar()
    return int(fila)


def guardar_pronostico(conn: Connection, corrida_id: int,
                       filas: Sequence[dict]) -> None:
    if not filas:
        return
    conn.execute(text("""
        INSERT INTO planificacion.Pronostico
            (CorridaID, SkillID, Intervalo, LlamadasTotal, Asignacion,
             LlamadasAcme, LlamadasBase, TmoSeg)
        VALUES (:corrida, :skill, :momento, :total, :asignacion, :acme, :base, :tmo)
    """), [{"corrida": corrida_id, **f} for f in filas])


def guardar_requerimiento(conn: Connection, corrida_id: int,
                          reqs: Sequence[pl.RequerimientoPool]) -> None:
    if not reqs:
        return
    # Las dos columnas de comparación con la malla las agrega la migración
    # 2026-09-03b. Se mira si están antes de armar el INSERT: con la primera
    # migración sola el planificador tiene que seguir corriendo, nada más que sin
    # la columna de citados.
    con_malla = _tiene_columna(conn, "planificacion.Requerimiento",
                               "OperadoresPlanificados")
    columnas = ("CorridaID, PoolID, Intervalo, Llamadas, TmoSeg, Trafico, "
                "OperadoresLinea, OperadoresPlanificar, NdsContractual, "
                "NdsAtendidas, NdsEntrantes, Abandono, Ocupacion, Motivo")
    valores = (":corrida, :pool, :momento, :llamadas, :tmo, :trafico, "
               ":linea, :planificar, :nds_c, :nds_at, :nds_en, :aband, :ocup, :motivo")
    if con_malla:
        columnas += ", OperadoresPlanificados, Brecha"
        valores += ", :citados, :brecha"
    con_asa = _tiene_columna(conn, "planificacion.Requerimiento", "AsaSeg")
    if con_asa:
        columnas += ", AsaSeg, NivelAtencionB"
        valores += ", :asa, :nab"
    # Las del refuerzo las agrega la migración 2026-09-14.
    con_refuerzo = _tiene_columna(conn, "planificacion.Requerimiento",
                                  "RefuerzoDisponible")
    if con_refuerzo:
        columnas += ", RefuerzoDisponible, RefuerzoCubre"
        valores += ", :ref_disp, :ref_cubre"
    # Los citados en equivalentes por antigüedad los agrega la 2026-09-15c.
    con_equivalentes = _tiene_columna(conn, "planificacion.Requerimiento",
                                      "CitadosEquivalentes")
    if con_equivalentes:
        columnas += ", CitadosEquivalentes"
        valores += ", :citados_eq"

    filas = [{
        "corrida": corrida_id, "pool": r.pool_id, "momento": r.momento,
        "llamadas": round(r.llamadas, 2), "tmo": round(r.tmo_seg, 2),
        "trafico": round(r.trafico, 3),
        "linea": r.operadores_en_linea, "planificar": r.operadores_a_planificar,
        "nds_c": round(r.nds_contractual, 4),
        "nds_at": round(r.nds_sobre_atendidas, 4),
        "nds_en": round(r.nds_sobre_entrantes, 4),
        "aband": round(r.abandono, 4), "ocup": round(r.ocupacion, 4),
        "motivo": r.motivo,
        **({"citados": r.planificados, "brecha": r.brecha} if con_malla else {}),
        # NULL y no un 99999 de relleno: en un intervalo sin llamadas —o con la
        # cola inestable— no hay una espera media, y un número inventado se
        # promedia, se grafica y se lee como si significara algo.
        **({"asa": round(r.asa_seg, 2) if math.isfinite(r.asa_seg) else None,
            "nab": round(r.nivel_atencion_b, 4)} if con_asa else {}),
        **({"ref_disp": r.refuerzo_disponible, "ref_cubre": r.refuerzo_cubre}
           if con_refuerzo else {}),
        **({"citados_eq": r.planificados_equivalentes} if con_equivalentes else {}),
    } for r in reqs]
    conn.execute(text(f"INSERT INTO planificacion.Requerimiento ({columnas}) "
                      f"VALUES ({valores})"), filas)


def cerrar_corrida(conn: Connection, corrida_id: int, campana_id: int,
                   horizonte: str, metricas: dict, estado: str = "OK") -> None:
    """Marca la corrida como terminada y, si salió bien, la deja como vigente.

    El índice único deja una sola vigente por campaña y horizonte, así que hay que
    bajar la anterior antes de subir esta."""
    if estado == "OK":
        conn.execute(text("""
            UPDATE planificacion.Corrida SET EsVigente = 0
            WHERE CampanaID = :c AND Horizonte = :h AND EsVigente = 1
        """), {"c": campana_id, "h": horizonte})
    conn.execute(text("""
        UPDATE planificacion.Corrida
        SET Estado = :e, Metricas = :m, TerminadoEn = SYSUTCDATETIME(),
            EsVigente = CASE WHEN :e = 'OK' THEN 1 ELSE 0 END
        WHERE CorridaID = :id
    """), {"e": estado, "m": json.dumps(metricas, default=str), "id": corrida_id})


def corrida_vigente(conn: Connection, campana_id: int,
                    horizonte: str = "operativo") -> Optional[dict]:
    fila = conn.execute(text("""
        SELECT CorridaID, Horizonte, Desde, Hasta, Modelo, Metricas, CreadoEn, TerminadoEn, CreadoPor
        FROM planificacion.Corrida
        WHERE CampanaID = :c AND Horizonte = :h AND EsVigente = 1
    """), {"c": campana_id, "h": horizonte}).mappings().fetchone()
    if not fila:
        return None
    datos = dict(fila)
    if datos.get("Metricas"):
        try:
            datos["Metricas"] = json.loads(datos["Metricas"])
        except (ValueError, TypeError):
            datos["Metricas"] = None
    return datos


def leer_requerimiento(conn: Connection, corrida_id: int) -> List[dict]:
    extra_malla = ("r.OperadoresPlanificados, r.Brecha"
                   if _tiene_columna(conn, "planificacion.Requerimiento",
                                     "OperadoresPlanificados")
                   else "NULL AS OperadoresPlanificados, NULL AS Brecha")
    extra_malla += (", r.AsaSeg, r.NivelAtencionB"
                    if _tiene_columna(conn, "planificacion.Requerimiento", "AsaSeg")
                    else ", NULL AS AsaSeg, NULL AS NivelAtencionB")
    extra_malla += (", r.RefuerzoDisponible, r.RefuerzoCubre"
                    if _tiene_columna(conn, "planificacion.Requerimiento",
                                      "RefuerzoDisponible")
                    else ", NULL AS RefuerzoDisponible, NULL AS RefuerzoCubre")
    extra_malla += (", r.CitadosEquivalentes"
                    if _tiene_columna(conn, "planificacion.Requerimiento",
                                      "CitadosEquivalentes")
                    else ", NULL AS CitadosEquivalentes")
    filas = conn.execute(text(f"""
        SELECT r.PoolID, p.Nombre AS Pool, r.Intervalo, r.Llamadas, r.TmoSeg,
               r.Trafico, r.OperadoresLinea, r.OperadoresPlanificar,
               r.NdsContractual, r.NdsAtendidas, r.NdsEntrantes, r.Abandono,
               r.Ocupacion, r.Motivo, {extra_malla}
        FROM planificacion.Requerimiento r
        JOIN planificacion.Pool p ON p.PoolID = r.PoolID
        WHERE r.CorridaID = :id
        ORDER BY r.Intervalo, p.Nombre
    """), {"id": corrida_id}).mappings().all()
    return [_con_faltante_neto(dict(f)) for f in filas]


def _con_faltante_neto(fila: dict) -> dict:
    """Lo que sigue faltando después de pasar al refuerzo, ya calculado: que la
    pantalla y la planilla no tengan que rearmar la cuenta cada una a su manera."""
    brecha, cubre = fila.get("Brecha"), fila.get("RefuerzoCubre")
    fila["FaltanteNeto"] = (max(-int(brecha), 0) - int(cubre)
                            if brecha is not None and cubre is not None else None)
    return fila


def leer_pronostico(conn: Connection, corrida_id: int) -> List[dict]:
    filas = conn.execute(text("""
        SELECT SkillID, Intervalo, LlamadasTotal, Asignacion, LlamadasAcme,
               LlamadasBase, TmoSeg
        FROM planificacion.Pronostico
        WHERE CorridaID = :id
        ORDER BY Intervalo, SkillID
    """), {"id": corrida_id}).mappings().all()
    return [dict(f) for f in filas]


# =========================================================================
# CONFIGURACIÓN EDITABLE
# =========================================================================
# Todo lo que la pantalla puede cambiar. Nada de esto es un valor cableado en el
# código: los objetivos, los pools, quién los compone, las franjas de
# disponibilidad y el origen de la gente en RRHH salen de estas tablas.
#
# Las escrituras son deliberadamente de "reemplazo completo" para las listas
# (franjas y origen del pool): una edición parcial de un conjunto chico obliga a
# resolver altas, bajas y modificaciones en el cliente, y ahí es donde aparecen
# los estados a medias.

def guardar_campana(conn: Connection, campana_id: int, datos: dict,
                    usuario: Optional[int]) -> None:
    """Parámetros generales de la campaña.

    Tocar la paciencia o el shrinkage a mano marca el origen como 'manual'. Eso
    ya NO bloquea a la calibración —el botón pisa igual y avisa qué pisó, ver
    `aplicar_calibracion`— pero sigue sirviendo para que la pantalla muestre de
    dónde salió cada número.
    """
    # Se toma en cuenta la PRESENCIA de la clave y no que su valor no sea None:
    # `max_ocupacion: null` tiene que poder significar "sacale el techo", y con
    # `if v is not None` no había manera de volver atrás una vez puesto.
    equivalencias = {
        "intervalo_min": "IntervaloMin",
        "max_ocupacion": "MaxOcupacion",
        "shrinkage": "ShrinkageDefault",
        "paciencia_seg": "PacienciaSeg",
        "paciencia_horizonte_seg": "PacienciaHorizonteSeg",
        "semanas_base": "SemanasBase",
        "dias_nivel": "DiasNivelReciente",
        "nivel_por_tipo_de_dia": "NivelPorTipoDeDia",
        "reparto_deriva_dias": "RepartoDerivaDias",
        "reparto_deriva_tope": "RepartoDerivaTope",
        "reparto_tipo_dia_dias": "RepartoTipoDiaDias",
        "reparto_tipo_dia_tope": "RepartoTipoDiaTope",
        "combinar_cliente": "CombinarCliente",
        "nivel_gbdt": "NivelGbdt",
        "nivel_gbdt_peso": "NivelGbdtPeso",
        "clima_elasticidad_tipo_dia": "ClimaElasticidadTipoDia",
        "shrinkage_no_habil": "ShrinkageNoHabil",
        "shrinkage_feriado": "ShrinkageFeriado",
        "break_min_por_hora": "BreakMinPorHora",
        # Las dos en null apagan el redondeo para abajo: el null es un valor.
        "redondeo_abajo_desde": "RedondeoAbajoDesde",
        "redondeo_abajo_hasta": "RedondeoAbajoHasta",
        "feriado_como_sabado": "FeriadoComoSabado",
        # null = el puente vuelve a ser un feriado más.
        "puente_factor": "PuenteFactor",
        "persistencia_peso_hoy": "PersistenciaPesoHoy",
        "persistencia_peso_resto": "PersistenciaPesoResto",
        "persistencia_dias": "PersistenciaDias",
        # null = sin reescalado intradía.
        "intradia_desde_hora": "IntradiaDesdeHora",
        # null = forma del perfil de `semanas_base` semanas.
        "forma_dias": "FormaDias",
        "persistencia_saltea_eventos": "PersistenciaSalteaEventos",
        "ancla_mensual_peso": "AnclaMensualPeso",
        "ancla_mensual_desde_dias": "AnclaMensualDesdeDias",
        "clima_activo": "ClimaActivo",
        "clima_lat": "ClimaLat",
        "clima_lon": "ClimaLon",
        "nota": "Nota",
    }
    campos = {col: datos[clave] for clave, col in equivalencias.items()
              if clave in datos}
    # Sin la migración 2026-09-22 estas columnas no existen, y la pantalla manda
    # IntradiaDesdeHora siempre (en null): sin este filtro, guardar la campaña
    # daría error hasta aplicarla.
    if not _tiene_columna(conn, "planificacion.Campana", "PersistenciaPesoHoy"):
        for col in ("FeriadoComoSabado", "PuenteFactor", "PersistenciaPesoHoy",
                    "PersistenciaPesoResto", "PersistenciaDias", "IntradiaDesdeHora"):
            campos.pop(col, None)
    # Lo mismo con las de la 2026-09-24e (la pantalla manda FormaDias siempre).
    if not _tiene_columna(conn, "planificacion.Campana", "FormaDias"):
        for col in ("FormaDias", "PersistenciaSalteaEventos", "AnclaMensualPeso",
                    "AnclaMensualDesdeDias"):
            campos.pop(col, None)
    # Estos tres no admiten nulo en la tabla: si vienen vacíos es que el cliente
    # no los mandó, no que se los quiera borrar.
    for obligatorio in ("IntervaloMin", "ShrinkageDefault",
                        "SemanasBase", "DiasNivelReciente", "ClimaActivo",
                        "NivelPorTipoDeDia", "RepartoDerivaDias",
                        "RepartoDerivaTope", "RepartoTipoDiaDias",
                        "RepartoTipoDiaTope", "CombinarCliente",
                        "NivelGbdt", "NivelGbdtPeso",
                        "ClimaElasticidadTipoDia", "BreakMinPorHora",
                        "FeriadoComoSabado", "PersistenciaPesoHoy",
                        "PersistenciaPesoResto", "PersistenciaDias",
                        "PersistenciaSalteaEventos", "AnclaMensualPeso",
                        "AnclaMensualDesdeDias"):
        if campos.get(obligatorio) is None:
            campos.pop(obligatorio, None)
    sets = [f"{k} = :{k}" for k in campos]
    if datos.get("paciencia_seg") is not None:
        sets.append("PacienciaOrigen = 'manual'")
    if datos.get("shrinkage") is not None:
        sets.append("ShrinkageOrigen = 'manual'")
    if not sets:
        return
    sets += ["ActualizadoPor = :usuario", "ActualizadoEn = SYSUTCDATETIME()"]
    conn.execute(text(f"""
        UPDATE planificacion.Campana SET {', '.join(sets)} WHERE CampanaID = :c
    """), {**campos, "usuario": usuario, "c": campana_id})


def guardar_pool(conn: Connection, campana_id: int, datos: dict) -> int:
    """Alta o edición de un pool. Devuelve el PoolID."""
    pool_id = datos.get("pool_id")
    if pool_id:
        filas = conn.execute(text("""
            UPDATE planificacion.Pool
            SET Nombre = :n, MinOperadores = :m, Activo = :a, Nota = :nota
            WHERE PoolID = :id AND CampanaID = :c
        """), {"id": pool_id, "c": campana_id, "n": datos["nombre"],
               "m": datos.get("min_operadores", 0), "a": datos.get("activo", True),
               "nota": datos.get("nota")}).rowcount
        if not filas:
            raise ValueError("El pool no existe en esta campaña.")
        return int(pool_id)

    return int(conn.execute(text("""
        INSERT INTO planificacion.Pool (CampanaID, Nombre, MinOperadores, Activo, Nota)
        OUTPUT INSERTED.PoolID
        VALUES (:c, :n, :m, :a, :nota)
    """), {"c": campana_id, "n": datos["nombre"],
           "m": datos.get("min_operadores", 0), "a": datos.get("activo", True),
           "nota": datos.get("nota")}).scalar())


def guardar_skill(conn: Connection, campana_id: int, skill_id: int,
                  datos: dict) -> None:
    """Objetivos de un skill y a qué pool pertenece.

    `pool_id = None` lo saca del dimensionamiento: es lo que corresponde para las
    colas sin volumen, que si no aparecen en la pantalla pidiendo cobertura mínima
    para llamadas que no existen.
    """
    planilla, extra = "", {}
    if _tiene_columna(conn, "planificacion.Skill", "Prioridad"):
        planilla += ", Prioridad = :prio"
        extra["prio"] = 1 if datos.get("prioridad") else 0
    if _tiene_columna(conn, "planificacion.Skill", "MaxAsaSeg"):
        planilla += (", MaxAsaSeg = :asa, ObjetivoNds2 = :nds2, "
                     "UmbralSeg2 = :umbral2, MinNivelAtencionB = :nab")
        extra.update({"asa": datos.get("max_asa_seg"),
                 "nds2": datos.get("objetivo_nds_2"),
                      "umbral2": datos.get("umbral_seg_2"),
                      "nab": datos.get("min_nivel_atencion_b")})

    # Se arma con f-string, igual que el resto del módulo: dejar un placeholder
    # dentro del literal SQL rompe la validación de sintaxis, que extrae las
    # consultas del fuente y las manda a parsear tal cual están escritas.
    filas = conn.execute(text(f"""
        UPDATE planificacion.Skill
        SET PoolID = :pool, ObjetivoNds = :nds, UmbralSeg = :umbral,
            MaxAbandono = :aband, PacienciaSeg = :pac, Activo = :activo,
            Nota = :nota{planilla}
        WHERE CampanaID = :c AND SkillID = :s
    """), {
           "c": campana_id, "s": skill_id, "pool": datos.get("pool_id"),
           "nds": datos.get("objetivo_nds"), "umbral": datos.get("umbral_seg"),
           "aband": datos.get("max_abandono"), "pac": datos.get("paciencia_seg"),
           "activo": datos.get("activo", True), "nota": datos.get("nota"),
           **extra}).rowcount
    if not filas:
        raise ValueError(f"El skill {skill_id} no está dado de alta en la campaña.")


def guardar_disponibilidad(conn: Connection, campana_id: int,
                           franjas: Sequence[dict]) -> None:
    """Reemplaza las franjas de disponibilidad de la campaña.

    Se valida que no se solapen dentro del mismo día: dos franjas pisándose
    dejarían el factor a merced del orden de lectura, que es exactamente el tipo
    de ambigüedad que después nadie puede explicar.
    """
    por_dia: Dict[int, List[Tuple[int, int]]] = {}
    for f in franjas:
        desde, hasta = int(f["hora_desde"]), int(f["hora_hasta"])
        if not (0 <= desde < hasta <= 24):
            raise ValueError(f"Franja horaria inválida: {desde} a {hasta}.")
        if not (0 < float(f["factor"]) <= 1):
            raise ValueError("El factor de disponibilidad tiene que estar entre 0 y 1.")
        dia = int(f.get("dia_semana", 0))
        for a, b in por_dia.get(dia, []):
            if desde < b and a < hasta:
                raise ValueError(
                    f"Las franjas {a}-{b} y {desde}-{hasta} se solapan en el mismo día.")
        por_dia.setdefault(dia, []).append((desde, hasta))

    conn.execute(text("DELETE FROM planificacion.Disponibilidad WHERE CampanaID = :c"),
                 {"c": campana_id})
    if not franjas:
        return
    conn.execute(text("""
        INSERT INTO planificacion.Disponibilidad
            (CampanaID, PoolID, DiaSemana, HoraDesde, HoraHasta, Factor, Origen, Muestras)
        VALUES (:c, :pool, :dia, :desde, :hasta, :factor, :origen, :muestras)
    """), [{"c": campana_id, "pool": f.get("pool_id"),
            "dia": int(f.get("dia_semana", 0)), "desde": int(f["hora_desde"]),
            "hasta": int(f["hora_hasta"]), "factor": float(f["factor"]),
            "origen": f.get("origen", "manual"), "muestras": f.get("muestras")}
           for f in franjas])


def guardar_pool_origen(conn: Connection, pool_id: int,
                        campanas_rrhh: Sequence[dict]) -> None:
    """Reemplaza las sub-campañas de RRHH que aportan gente a un pool."""
    conn.execute(text("DELETE FROM planificacion.PoolOrigen WHERE PoolID = :p"),
                 {"p": pool_id})
    if not campanas_rrhh:
        return
    conn.execute(text("""
        INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota)
        VALUES (:p, :rrhh, :nota)
    """), [{"p": pool_id, "rrhh": int(c["campana_rrhh_id"]), "nota": c.get("nota")}
           for c in campanas_rrhh])


def campanas_rrhh(conn: Connection, cliente: Optional[str] = None) -> List[dict]:
    """Catálogo de sub-campañas de RRHH, para el selector de la pantalla."""
    sql = "SELECT id, cliente, campana, sub_campana FROM dbo.campanas"
    params = {}
    if cliente:
        sql += " WHERE cliente = :cliente"
        params["cliente"] = cliente
    filas = conn.execute(text(sql + " ORDER BY cliente, sub_campana"), params).mappings()
    return [dict(f) for f in filas]


def guardar_codigo_payroll(conn: Connection, codigo: str, clase: str,
                           descripcion: Optional[str] = None) -> None:
    """Clasifica un código de RRHH. Es lo que hace auditable el shrinkage: si el
    número no cierra, se puede ver exactamente qué código lo está moviendo."""
    if clase not in ("piso", "ausente", "capacitacion", "licencia", "otro"):
        raise ValueError(f"Clase inválida: {clase}")
    conn.execute(text("""
        MERGE planificacion.CodigoPayroll AS destino
        USING (SELECT :codigo AS Codigo) AS origen ON destino.Codigo = origen.Codigo
        WHEN MATCHED THEN UPDATE SET Clase = :clase, Descripcion = :desc
        WHEN NOT MATCHED THEN INSERT (Codigo, Clase, Descripcion)
                                VALUES (:codigo, :clase, :desc);
    """), {"codigo": codigo, "clase": clase, "desc": descripcion})


def codigos_payroll(conn: Connection) -> List[dict]:
    if not _tiene_tabla(conn, "planificacion.CodigoPayroll"):
        return []
    filas = conn.execute(text("""
        SELECT Codigo, Clase, Descripcion FROM planificacion.CodigoPayroll
        ORDER BY Clase, Codigo
    """)).mappings()
    return [dict(f) for f in filas]


# Qué puestos aportan horas de piso a las sub-campañas del pool, y cuáles de esos
# cuentan como dotación telefónica. Se listan los que TIENEN horas y no el
# catálogo entero: la pregunta que contesta la pantalla no es "qué puestos
# existen" sino "a quién estoy contando y a quién estoy dejando afuera, y cuánto
# pesa cada uno". Un puesto con horas y sin clasificar sale con `en_malla` en
# None, que es la alarma.
_PUESTOS_MALLA = """
    SELECT o.puesto_id                          AS puesto_id,
           MAX(pu.puesto)                       AS nombre,
           {en_malla}                           AS en_malla,
           COUNT(DISTINCT p.id_operadores)      AS personas,
           SUM(ISNULL(p.horas_programadas, 0))  AS horas
    FROM dbo.payroll p
    JOIN dbo.operadores o ON o.id = p.id_operadores
    JOIN planificacion.CodigoPayroll c ON c.Codigo = p.codigo AND c.Clase = 'piso'
    LEFT JOIN dbo.puestos pu ON pu.id = o.puesto_id
    {join}
    WHERE p.fecha >= :desde AND p.fecha < :hasta
      AND o.campana_id IN :campanas
    GROUP BY o.puesto_id
"""


def puestos_de_la_malla(conn: Connection, pool_ids: Sequence[int],
                        desde: date, hasta: date) -> Dict[str, object]:
    """Quién está aportando horas de piso y quién de esos cuenta en la malla.

    Sin la migración 2026-09-09e devuelve la lista igual, con `en_malla` en None
    y `disponible` en False: hay que poder ver a quién se está contando de más
    ANTES de aplicar la migración, que es la información con la que alguien
    decide aplicarla.
    """
    campanas: List[int] = []
    for pool_id in pool_ids:
        campanas.extend(campanas_rrhh_del_pool(conn, pool_id))
    campanas = sorted(set(campanas))
    if not campanas or not _tiene_tabla(conn, "planificacion.CodigoPayroll"):
        return {"disponible": False, "puestos": [],
                "motivo": "el pool no tiene sub-campañas de RRHH asociadas"}

    hay_tabla = _tiene_tabla(conn, "planificacion.PuestoMalla")
    sql = _PUESTOS_MALLA.format(
        en_malla=("MAX(CAST(m.EnMalla AS tinyint))" if hay_tabla else "NULL"),
        join=("LEFT JOIN planificacion.PuestoMalla m ON m.PuestoID = o.puesto_id"
              if hay_tabla else ""))
    filas = conn.execute(_expandir(sql, "campanas"),
                         {"desde": desde, "hasta": hasta, "campanas": campanas}
                         ).mappings().all()
    puestos = [{"puesto_id": f["puesto_id"],
                "nombre": f["nombre"] or "(sin puesto)",
                "en_malla": None if f["en_malla"] is None else bool(f["en_malla"]),
                "personas": int(f["personas"] or 0),
                "horas": round(float(f["horas"] or 0), 1)}
               for f in filas]
    puestos.sort(key=lambda x: -x["horas"])
    return {
        "disponible": hay_tabla,
        "motivo": None if hay_tabla else (
            "falta correr scripts/migrations/2026-09-09e_planificador_puestos_malla.sql"),
        "puestos": puestos,
        "horas_en_malla": round(sum(p["horas"] for p in puestos
                                    if p["en_malla"]), 1),
        "horas_fuera": round(sum(p["horas"] for p in puestos
                                 if not p["en_malla"]), 1),
        "sin_clasificar": [p["nombre"] for p in puestos
                           if p["en_malla"] is None and p["horas"] > 0],
    }


def guardar_puesto_malla(conn: Connection, puesto_id: int, en_malla: bool,
                         nota: Optional[str] = None) -> None:
    """Dice si un puesto cuenta como dotación telefónica."""
    if not _tiene_tabla(conn, "planificacion.PuestoMalla"):
        raise MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-09e_planificador_puestos_malla.sql")
    conn.execute(text("""
        MERGE planificacion.PuestoMalla AS destino
        USING (SELECT :id AS PuestoID) AS origen ON destino.PuestoID = origen.PuestoID
        WHEN MATCHED THEN UPDATE SET EnMalla = :en, Nota = :nota
        WHEN NOT MATCHED THEN INSERT (PuestoID, EnMalla, Nota)
                                VALUES (:id, :en, :nota);
    """), {"id": int(puesto_id), "en": 1 if en_malla else 0, "nota": nota})


ORIGENES_SHRINKAGE_MEDIDO = ("payroll", "presencia")


def aplicar_calibracion(conn: Connection, campana_id: int,
                        paciencia_seg: Optional[int] = None,
                        shrinkage: Optional[dict] = None) -> List[str]:
    """Deja como vigentes los valores que midió la calibración.

    Es un paso explícito y no automático: la paciencia y el shrinkage mueven toda
    la dotación, así que el número medido se muestra al lado del vigente y alguien
    aprieta el botón.

    PISA LO PUESTO A MANO, Y DEVUELVE QUÉ PISÓ
    ------------------------------------------
    Antes no lo hacía, y era peor: un valor tocado a mano una vez dejaba el botón
    sin efecto para siempre, en silencio. Alguien apretaba «dejar vigentes los
    valores medidos», veía el número medido en pantalla, y el que se usaba para
    dimensionar seguía siendo otro. Un botón que a veces no hace nada y no lo
    dice es peor que no tenerlo.

    Ahora pisa y devuelve la lista de lo que pisó, para que la pantalla lo avise.
    La advertencia importa de verdad cuando hay varios valores tocados a mano: en
    ese caso el botón deja de tener sentido como atajo y conviene revisarlos uno
    por uno.
    """
    origen = conn.execute(text("""
        SELECT ISNULL(PacienciaOrigen, '') AS pac, ISNULL(ShrinkageOrigen, '') AS sh
        FROM planificacion.Campana WHERE CampanaID = :c
    """), {"c": campana_id}).mappings().fetchone() or {}
    pisados: List[str] = []

    # Los dos parámetros se aplican por separado: cada uno mira SU propio origen.
    if paciencia_seg:
        if origen.get("pac") == "manual":
            pisados.append("paciencia")
        conn.execute(text("""
            UPDATE planificacion.Campana
            SET PacienciaSeg = :pac, PacienciaOrigen = 'km'
            WHERE CampanaID = :c
        """), {"c": campana_id, "pac": int(paciencia_seg)})

    if shrinkage and shrinkage.get("shrinkage") is not None:
        if origen.get("sh") == "manual":
            pisados.append("shrinkage")
        # De qué medición sale: los códigos de RRHH ('payroll') o la presencia en
        # la línea ('presencia', ver `nivel_de_presencia`). Lista cerrada: lo que
        # no se reconoce queda como el de siempre.
        de_donde = shrinkage.get("origen")
        de_donde = de_donde if de_donde in ORIGENES_SHRINKAGE_MEDIDO else "payroll"
        # El shrinkage por tipo de día se aplica JUNTO con el general y no
        # aparte: son la misma medición partida, y dejar uno viejo al lado del
        # otro nuevo es peor que no tener ninguno.
        por_dia, extra = "", {}
        if _tiene_columna(conn, "planificacion.Campana", "ShrinkageFeriado"):
            por_dia = ", ShrinkageNoHabil = :shnh, ShrinkageFeriado = :shf"
            extra = {"shnh": shrinkage.get("no_habil"),
                     "shf": shrinkage.get("feriado")}
        conn.execute(text(f"""
            UPDATE planificacion.Campana
            SET ShrinkageDefault = :sh, ShrinkageAusentismo = :sha,
                ShrinkageCapacitacion = :shc, ShrinkageOrigen = :sho,
                ShrinkageMedidoEn = SYSUTCDATETIME(){por_dia}
            WHERE CampanaID = :c
        """), {"c": campana_id, "sh": float(shrinkage["shrinkage"]), "sho": de_donde,
               "sha": shrinkage.get("ausentismo"),
               "shc": shrinkage.get("capacitacion"), **extra})
    return pisados


def guardar_paciencia_por_skill(conn: Connection, campana_id: int,
                                por_skill: Dict[int, Optional[int]]) -> int:
    """Deja la paciencia medida en cada skill.

    Existe porque la paciencia NO es una sola por campaña: el que se quedó sin
    luz espera mucho más que un electrodependiente, y son colas distintas. La de
    la campaña queda como valor por defecto para el skill que no tenga el suyo.

    Un valor en None LIMPIA el de ese skill, que vuelve a caer en el de la
    campaña. Es deliberado: sin eso, un skill que dejó de tener muestra
    suficiente se quedaría con una paciencia vieja para siempre.
    """
    tocados = 0
    for skill_id, segundos in por_skill.items():
        tocados += conn.execute(text("""
            UPDATE planificacion.Skill SET PacienciaSeg = :p
            WHERE CampanaID = :c AND SkillID = :s
        """), {"c": campana_id, "s": int(skill_id),
               "p": int(segundos) if segundos else None}).rowcount or 0
    return tocados


def guardar_pesos_combinacion(conn: Connection, campana_id: int,
                              peso_habil: Optional[float],
                              peso_no_habil: Optional[float]) -> None:
    """Deja vigentes los pesos del pronóstico del cliente que midió el backtest.

    No prende la combinación: eso es una decisión aparte (`CombinarCliente`), y
    tiene que ser posible medir el peso sin cambiar el pronóstico que la
    operación está usando.
    """
    if not _tiene_columna(conn, "planificacion.Campana", "CombinarClientePesoHabil"):
        raise MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-09_planificador_reparto_cliente.sql")
    conn.execute(text("""
        UPDATE planificacion.Campana
        SET CombinarClientePesoHabil = :ph, CombinarClientePesoNoHabil = :pn,
            CombinarClienteMedidoEn = SYSUTCDATETIME()
        WHERE CampanaID = :c
    """), {"c": campana_id,
           "ph": None if peso_habil is None else float(peso_habil),
           "pn": None if peso_no_habil is None else float(peso_no_habil)})


# =========================================================================
# DEMANDA TOTAL DEL CLIENTE — el 100% de las llamadas, no sólo nuestra porción
# =========================================================================
# `dbo.[Voltara Enerval informe IVR]` es la descarga completa del portal de Enerval: 25,4
# millones de llamadas desde 2023-01-01, con BPO y Skill poblados para TODOS los
# contact centers. Es lo que destraba el problema de fondo del pronóstico.
#
# QUÉ CAMBIA
# ----------
# Hasta ahora la serie era `demanda_de_Voltara x nuestro_share`, mezcladas y sin
# forma de separarlas, así que un cambio de reparto rompía el pronóstico por
# construcción. Y el reparto cambió: el 2026-09-01, de un día para el otro, Acme
# pasó de ~32% a ~49% de las llamadas ruteadas. Con esta tabla el pronóstico se
# hace sobre la demanda TOTAL (que depende del clima, los feriados y los cortes,
# no de con quién tenga contrato Voltara) y el share se aplica aparte, con vigencia.
#
# QUÉ CUENTA COMO DEMANDA
# -----------------------
# Sólo las llamadas que llegaron a una COLA de un contact center (`Cola IS NOT
# NULL`). El resto de la tabla NO es demanda de los BPOs:
#   - `Resultado IVR = 'AGENT'` sin BPO y sin cola: la ruteó el IVR pero nunca se
#     conectó (Tiempo Total == Tiempo IVR y Talk = 0 en 185.667 de 185.688 casos
#     de agosto 2026). Es demanda perdida antes de llegar a nadie.
#   - DISCONNECT / SURVEY / nulos: se resolvieron o se cortaron dentro del IVR.
# Contarlas como demanda del pool inflaría el dimensionamiento con llamadas que
# ningún operador podría haber atendido.
#
# EL TMO ES EL NUESTRO, NO EL PROMEDIO
# ------------------------------------
# El volumen se pronostica sobre el total, pero el tiempo de atención se toma sólo
# de BPO-04: nuestros operadores tardan lo que tardan. En agosto 2026, Comercial
# nos llevaba 333s y a BPO-05 402s; usar el promedio de los dos sobredimensionaría.

BPO_ACME = "BPO-04"
TABLA_DEMANDA_TOTAL = "dbo.Voltara Enerval informe IVR"


class FuenteVoltaraTotal:
    """Demanda total de Voltara por intervalo y skill, con nuestra porción aparte."""

    # El skill viene como texto ('Emergencias') y el resto del planificador
    # trabaja con el id del normalizador; el UPPER() los cruza sin huérfanos por
    # ninguno de los dos lados (verificado sobre agosto 2026, los 9 skills).
    # El bucketeo va en una subconsulta y no repetido en el SELECT y el GROUP BY:
    # con parámetros de por medio, SQL Server no reconoce las dos expresiones como
    # la misma y rechaza la consulta con el error 8120.
    DEMANDA = """
        SELECT momento, skill_id,
               COUNT(*)                                          AS total,
               SUM(CASE WHEN es_acme = 1 THEN 1 ELSE 0 END)      AS acme,
               SUM(CASE WHEN es_acme = 1 AND atendida = 1 THEN 1 ELSE 0 END)
                                                                 AS acme_atendidas,
               SUM(CASE WHEN es_acme = 1 AND atendida = 1 THEN trabajo ELSE 0 END)
                                                                 AS acme_trabajo
        FROM (
            SELECT DATEADD(minute,
                       (DATEDIFF(minute, '2000-01-01', i.[Fecha de Inicio]) / :minutos) * :minutos,
                       CAST('2000-01-01' AS datetime))           AS momento,
                   n.[Skill ID]                                  AS skill_id,
                   CASE WHEN i.BPO = :bpo THEN 1 ELSE 0 END      AS es_acme,
                   CASE WHEN i.[Nombre de Agente] IS NOT NULL THEN 1 ELSE 0 END AS atendida,
                   ISNULL(i.[Duración Talk], 0)
                 + ISNULL(i.[Duración Hold], 0)
                 + ISNULL(i.[Duración ACW],  0)                  AS trabajo
            FROM [dbo].[Voltara Enerval informe IVR] i
            JOIN [dbo].[Voltara normalizador por Skill] n
              ON UPPER(n.Skill) = UPPER(i.Skill)
            WHERE i.Cola IS NOT NULL
              AND i.[Fecha de Inicio] >= :desde AND i.[Fecha de Inicio] < :hasta
        ) t
        GROUP BY momento, skill_id
    """

    # Share por día y skill, para ver cuándo cambió el reparto y con qué escalón.
    ASIGNACION = """
        SELECT CAST(i.[Fecha de Inicio] AS date)                      AS dia,
               n.[Skill ID]                                           AS skill_id,
               COUNT(*)                                               AS total,
               SUM(CASE WHEN i.BPO = :bpo THEN 1 ELSE 0 END)          AS acme
        FROM [dbo].[Voltara Enerval informe IVR] i
        JOIN [dbo].[Voltara normalizador por Skill] n
          ON UPPER(n.Skill) = UPPER(i.Skill)
        WHERE i.Cola IS NOT NULL
          AND i.[Fecha de Inicio] >= :desde AND i.[Fecha de Inicio] < :hasta
        GROUP BY CAST(i.[Fecha de Inicio] AS date), n.[Skill ID]
    """


FUENTES_TOTAL = {CAMPANA_VOLTARA: FuenteVoltaraTotal}


def hay_demanda_total(conn: Connection, campana_id: int) -> bool:
    """Si tenemos el 100% de las llamadas del cliente y no sólo nuestra porción.

    Mientras sea False, el pronóstico es de lo que recibimos nosotros y la
    asignación NO se aplica (aplicarla sobre una serie que ya la tiene adentro la
    contaría dos veces). En True, el pronóstico pasa a ser de la demanda del
    cliente y el share se aplica aparte.
    """
    if campana_id not in FUENTES_TOTAL:
        return False
    return _tiene_tabla(conn, TABLA_DEMANDA_TOTAL)


def serie_demanda_total(conn: Connection, campana_id: int, desde: date, hasta: date,
                        intervalo_min: int = 30
                        ) -> Dict[Tuple[datetime, int], Dict[str, float]]:
    """(momento, skill) -> demanda total del cliente, nuestra porción y nuestro TMO.

    `tmo` sale sólo de las llamadas que atendimos nosotros; los intervalos donde
    no atendimos ninguna quedan en 0 y el que los use tiene que caer al perfil.
    """
    fuente = FUENTES_TOTAL.get(campana_id)
    if fuente is None:
        raise ValueError(
            f"La campaña {campana_id} no tiene fuente de demanda total declarada.")

    filas = conn.execute(text(fuente.DEMANDA),
                         {"desde": desde, "hasta": hasta, "bpo": BPO_ACME,
                          "minutos": intervalo_min}).mappings()
    salida: Dict[Tuple[datetime, int], Dict[str, float]] = {}
    for f in filas:
        if f["skill_id"] is None:
            continue
        atendidas = float(f["acme_atendidas"] or 0)
        salida[(f["momento"], int(f["skill_id"]))] = {
            "total": float(f["total"] or 0),
            "acme": float(f["acme"] or 0),
            "tmo": (float(f["acme_trabajo"] or 0) / atendidas) if atendidas > 0 else 0.0,
        }
    return salida


def medir_asignacion(conn: Connection, campana_id: int, desde: date, hasta: date
                     ) -> Dict[str, object]:
    """Qué porcentaje de las llamadas ruteadas nos tocó, por día y por skill.

    Sirve para dos cosas: sembrar `planificacion.Asignacion` con la historia real,
    y detectar cuándo cambió el reparto. El cambio del 2026-09-01 aparece como un
    escalón limpio, de ~32% a ~49% de un día para el otro.
    """
    fuente = FUENTES_TOTAL.get(campana_id)
    if fuente is None:
        return {}

    filas = conn.execute(text(fuente.ASIGNACION),
                         {"desde": desde, "hasta": hasta, "bpo": BPO_ACME}).mappings().all()
    por_dia: Dict[date, List[int]] = {}
    por_skill: Dict[int, List[int]] = {}
    for f in filas:
        total, acme = int(f["total"] or 0), int(f["acme"] or 0)
        d = por_dia.setdefault(f["dia"], [0, 0])
        d[0] += total
        d[1] += acme
        s = por_skill.setdefault(int(f["skill_id"]), [0, 0])
        s[0] += total
        s[1] += acme

    diaria = {d: round(m / t, 4) for d, (t, m) in sorted(por_dia.items()) if t > 0}
    vigente = _ultimo_escalon(diaria)

    # El share POR SKILL del régimen vigente, no del período entero. Es la
    # diferencia entre un número útil y uno que no es ni el reparto viejo ni el
    # nuevo: promediando julio-agosto (32%) con septiembre (49%) sale ~36%, que
    # no rigió nunca.
    por_skill_vigente: Dict[int, List[int]] = {}
    if vigente:
        for f in filas:
            if f["dia"] >= vigente["desde"]:
                v = por_skill_vigente.setdefault(int(f["skill_id"]), [0, 0])
                v[0] += int(f["total"] or 0)
                v[1] += int(f["acme"] or 0)

    return {
        "por_dia": diaria,
        "por_skill": {k: round(m / t, 4) for k, (t, m) in por_skill.items() if t > 0},
        "por_skill_vigente": {k: round(m / t, 4)
                              for k, (t, m) in por_skill_vigente.items() if t > 0},
        "vigente": vigente,
    }


def _ultimo_escalon(diaria: Dict[date, float], tolerancia: float = 0.05
                    ) -> Optional[dict]:
    """El share actual, buscando hacia atrás hasta que el reparto cambia.

    Promediar los últimos 30 días daría un número que no es ni el viejo ni el
    nuevo justo después de un cambio de contrato — que es exactamente cuando hace
    falta el dato. Por eso se retrocede sólo mientras el share se mantenga dentro
    de la tolerancia del último día conocido.
    """
    if not diaria:
        return None
    dias = sorted(diaria)
    referencia = diaria[dias[-1]]
    desde = dias[-1]
    valores = []
    for d in reversed(dias):
        if abs(diaria[d] - referencia) > tolerancia:
            break
        valores.append(diaria[d])
        desde = d
    return {
        "porcentaje": round(sum(valores) / len(valores), 4),
        "desde": desde,
        "hasta": dias[-1],
        "dias": len(valores),
    }


def asignacion_configurada(conn: Connection, campana_id: int, desde: date,
                           hasta: date) -> List[dict]:
    """Los tramos de asignación cargados, que son los que manda el pronóstico."""
    filas = conn.execute(text("""
        SELECT AsignacionID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota
        FROM planificacion.Asignacion
        WHERE CampanaID = :c
          AND VigenteDesde <= :hasta
          AND (VigenteHasta IS NULL OR VigenteHasta >= :desde)
        ORDER BY VigenteDesde, SkillID
    """), {"c": campana_id, "desde": desde, "hasta": hasta}).mappings().all()
    return [dict(f) for f in filas]


def factor_de_asignacion(tramos: Sequence[dict], momento: datetime,
                         skill_id: int) -> Optional[float]:
    """El share que corresponde a ese momento y skill.

    Gana el tramo específico del skill sobre el general de la campaña; entre dos
    del mismo alcance, el que empezó después. Sin ningún tramo devuelve None, y el
    que llama decide: en el pronóstico eso significa "no puedo repartir esta
    demanda", que es más honesto que suponer un 100%.
    """
    dia = momento.date() if isinstance(momento, datetime) else momento
    candidatos = [
        t for t in tramos
        if t["VigenteDesde"] <= dia
        and (t["VigenteHasta"] is None or t["VigenteHasta"] >= dia)
        and (t["SkillID"] is None or t["SkillID"] == skill_id)
    ]
    if not candidatos:
        return None
    candidatos.sort(key=lambda t: (t["SkillID"] is not None, t["VigenteDesde"]))
    return float(candidatos[-1]["Porcentaje"])


def deriva_de_reparto(tramos: Sequence[dict],
                      diario: Dict[Tuple[date, int], Tuple[float, float]],
                      corte: date, skill_id: int, dias: int, tope: float
                      ) -> Optional[float]:
    """Cuánto se corrió el reparto real respecto del tramo, en los últimos días.

    El tramo dice el régimen ("nos mandan el 49%"); esto sigue el vaivén de
    todos los días alrededor de ese número, que no es chico: medido sobre el
    régimen 2025-06 a 2026-08, el share diario tiene un CV de 14,2% y va de
    0,298 a 0,441 entre el percentil 5 y el 95. Aun acertando exactamente la
    demanda del cliente, ese vaivén solo deja 9,8% de error diario sobre
    nuestras llamadas; seguirlo con siete días lo baja a 8,2%.

    Cada día se compara contra el tramo que regía ESE día, no contra el vigente
    al corte. Con un solo tramo de referencia, un escalón de reparto se leería
    como una deriva enorme justo cuando el tramo ya está bien: el 2026-09-01 el
    share pasó de 0,356 a 0,492, y los siete días previos medidos contra el
    tramo nuevo darían una corrección de 0,65 sobre un número correcto.

    Devuelve None cuando no hay con qué medir, y el que llama deja el tramo como
    está: es más honesto que corregir con una muestra vacía.
    """
    if dias <= 0:
        return None
    esperado = real = 0.0
    for (dia, sid), (total, nuestras) in diario.items():
        if sid != skill_id or not (corte - timedelta(days=dias) <= dia < corte):
            continue
        pct = factor_de_asignacion(tramos, dia, sid)
        if not pct:
            continue
        esperado += total * pct
        real += nuestras
    if esperado <= 0:
        return None
    return min(1 + tope, max(1 - tope, real / esperado))


def factor_reparto_no_habil(tramos: Sequence[dict],
                            diario: Dict[Tuple[date, int], Tuple[float, float]],
                            corte: date, dias: int, feriados: Sequence[date],
                            tope: float = 0.25, min_dias_no_habiles: int = 20
                            ) -> Optional[float]:
    """Cuánto se corre el reparto de un día NO hábil respecto de uno hábil.

    OJO: nace APAGADA (`CampanaCfg.reparto_tipo_dia_dias = 0`) porque medida sobre
    el pronóstico real EMPEORA. El comentario del dataclass tiene la tabla y la
    explicación; el resumen es que el reparto se aplica por skill, el fin de
    semana tiene otra mezcla de colas y los tramos por skill ya capturan la mayor
    parte del efecto, así que el factor lo cuenta dos veces. La función queda
    porque el mecanismo de fondo es real y está medido, y porque si el reparto
    vuelve a moverse esta es la perilla.

    EL EFECTO QUE MIDE, EN EL AGREGADO
    ----------------------------------
    El tramo de `planificacion.Asignacion` es un número por skill y por régimen,
    igual para los siete días de la semana. Sobre 464 días (2025-06 a 2026-09), el
    share de un sábado o un domingo es 6,6% menor que el de los hábiles de la
    misma quincena y el de un feriado 12,8% mayor (14 de 20 feriados por encima).
    El motivo es el VOLUMEN: la elasticidad de log(share) contra log(volumen) es
    +0,140, con correlación +0,34 en hábiles y +0,70 en feriados. Voltara nos
    desborda cuando tiene un día grande, y los fines de semana son chicos.

    Devuelve el multiplicador para los días no hábiles —los hábiles quedan en
    1,0— o None si no hay con qué medirlo, y en ese caso el que llama deja el
    tramo como está. El cociente va contra los HÁBILES de la misma ventana y no
    contra el tramo directamente: así no se pisa con `deriva_de_reparto`, que ya
    corrige el nivel general del share con los últimos días.
    """
    if dias <= 0:
        return None
    fer = set(feriados or ())
    acum: Dict[str, List[float]] = {"habil": [0.0, 0.0, 0.0], "no_habil": [0.0, 0.0, 0.0]}
    vistos: Dict[str, set] = {"habil": set(), "no_habil": set()}
    for (dia, sid), (total, nuestras) in diario.items():
        if not (corte - timedelta(days=dias) <= dia < corte):
            continue
        pct = factor_de_asignacion(tramos, dia, sid)
        if not pct:
            continue
        clave = pl.tipo_de_dia(dia, fer)
        par = acum[clave]
        par[0] += total * pct
        par[1] += nuestras
        vistos[clave].add(dia)

    if (acum["habil"][0] <= 0 or acum["no_habil"][0] <= 0
            or len(vistos["no_habil"]) < min_dias_no_habiles
            or len(vistos["habil"]) < min_dias_no_habiles):
        return None
    ratio_habil = acum["habil"][1] / acum["habil"][0]
    ratio_no_habil = acum["no_habil"][1] / acum["no_habil"][0]
    if ratio_habil <= 0:
        return None
    return min(1 + tope, max(1 - tope, ratio_no_habil / ratio_habil))


def reparto_diario(serie: Dict[Tuple[datetime, int], Dict[str, float]]
                   ) -> Dict[Tuple[date, int], Tuple[float, float]]:
    """(día, skill) -> (demanda total del cliente, lo que nos llegó a nosotros).

    Es la entrada de `deriva_de_reparto`, agregada por día porque el reparto se
    mide por día: a nivel intervalo el ruido tapa la señal.
    """
    salida: Dict[Tuple[date, int], List[float]] = {}
    for (momento, sid), v in serie.items():
        par = salida.setdefault((momento.date(), sid), [0.0, 0.0])
        par[0] += v.get("total", 0.0)
        par[1] += v.get("acme", 0.0)
    return {k: (v[0], v[1]) for k, v in salida.items()}


def guardar_asignacion(conn: Connection, campana_id: int, tramos: Sequence[dict],
                       usuario: Optional[int]) -> None:
    """Reemplaza los tramos de asignación de la campaña."""
    for t in tramos:
        pct = float(t["porcentaje"])
        # El 0 se admite: CNR y EMERGENCIAS-EMPRESARIAL son colas reales del
        # cliente que hoy se rutean enteras al otro BPO. Sin poder representarlo,
        # quedarían sin tramo y el pronóstico no podría distinguir "no nos mandan
        # nada" de "falta configurarlo".
        if not (0 <= pct <= 1):
            raise ValueError(f"El porcentaje de asignación tiene que estar entre 0 y 1: {pct}")
        if t.get("vigente_hasta") and t["vigente_hasta"] < t["vigente_desde"]:
            raise ValueError("Un tramo de asignación no puede terminar antes de empezar.")

    conn.execute(text("DELETE FROM planificacion.Asignacion WHERE CampanaID = :c"),
                 {"c": campana_id})
    if not tramos:
        return
    conn.execute(text("""
        INSERT INTO planificacion.Asignacion
            (CampanaID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota, CreadoPor)
        VALUES (:c, :skill, :desde, :hasta, :pct, :nota, :usuario)
    """), [{"c": campana_id, "skill": t.get("skill_id"), "desde": t["vigente_desde"],
            "hasta": t.get("vigente_hasta"), "pct": float(t["porcentaje"]),
            "nota": t.get("nota"), "usuario": usuario} for t in tramos])


def pronostico_del_dia(conn: Connection, corrida_id: int, dia: date) -> Dict[datetime, dict]:
    """El pronóstico de una corrida para un día, sumado por intervalo."""
    filas = conn.execute(text("""
        SELECT Intervalo, SUM(LlamadasAcme) AS acme, SUM(LlamadasTotal) AS total,
               AVG(CAST(Asignacion AS float)) AS asignacion
        FROM planificacion.Pronostico
        WHERE CorridaID = :id AND Intervalo >= :desde AND Intervalo < :hasta
        GROUP BY Intervalo ORDER BY Intervalo
    """), {"id": corrida_id, "desde": dia,
           "hasta": dia + timedelta(days=1)}).mappings().all()
    return {f["Intervalo"]: {"acme": float(f["acme"] or 0),
                             "total": float(f["total"]) if f["total"] is not None else None,
                             "asignacion": f["asignacion"]}
            for f in filas}
