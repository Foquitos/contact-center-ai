/* ============================================================================
   Vista — Llamadas "rebotadas" por el operador (Vantix / MySQL omnicanalidad)
   Fecha: 2026-07-27
   Autor: equipo Acme

   PARA QUÉ
   --------
   Detectar operadores que esquivan llamadas: la llamada entra a una COLA ACD,
   el ACD se la asigna a un operador, y el operador se la saca de encima —
   sin atenderla, o atendiéndola y transfiriéndola a los pocos segundos, sin
   margen para ningún tipo de gestión.

   Devuelve UNA FILA POR TRAMO DE OPERADOR sospechoso (no una por llamada): si
   la misma llamada rebota entre 4 operadores, salen las 4 filas, cada una con
   su Intento_Nro. Esa es la granularidad que sirve para señalar responsables.

   DE DÓNDE SALEN LOS DATOS
   ------------------------
   Linked server MYSQL_LINK -> omnicanalidad.eventdetailrecords (Mitrol/Vantix).
   Una llamada = un GlobalId = una secuencia de eventos ordenada por
   EventStartTime. Tipos de evento relevantes (omnicanalidad.eventtypes):
       1 = ACD / cola      2 = tramo de agente      3 = wrapup
       5 = consulta        6 = IVR
   Origin del evento tipo 1 es el IdCola (omnicanalidad.colas_acd).

   CÓMO SE RECONOCE CADA CASO (verificado sobre datos reales)
   ----------------------------------------------------------
   Universo: eventos tipo 2 (agente) cuyo evento ANTERIOR es tipo 1 (cola).
   Eso deja solo llamadas entrantes distribuidas por el ACD y descarta las
   salientes manuales (que no tienen tramo de cola previo).

   A) 'SIN ATENDER'  -> EventAnswerTime = '0000-00-00 00:00:00'
      El operador nunca descolgó. Dos formas de sacársela de encima:
        - vuelve a la cola: el evento siguiente es tipo 1 otra vez
          (EventCause 3 = "Sin respuesta": dejó sonar hasta el timeout de ring
          y el ACD la re-encoló). Es el caso masivo.
        - la deriva sin atender: el evento siguiente es IVR (tipo 6) o consulta
          (tipo 5), típicamente con EventCause 113 = "Llamada transferida a IVR".

   B) 'ATENDIDA CORTA' -> atendió y transfirió con menos de 5 s de conversación
      real (Segundos_Hablado = atención -> fin, descontando el hold).
      IMPORTANTE: en Vantix la transferencia de vuelta a la cola NO es un evento
      tipo 1 inmediato. El tramo del agente cierra con EventCause 113 y pasa
      por un evento IVR (tipo 6) que a su vez cierra con EventCause 112
      ("Llamada transferida a ACD") y recién ahí aparece el tipo 1 de la cola.
      Por eso Vuelve_A_Cola mira el evento siguiente Y el subsiguiente.

   NO SE INCLUYE (a propósito): el tramo corto que termina con EventCause 5/6 y
   evento siguiente wrapup (tipo 3). Eso es "atendió y CORTÓ", no transferencia.
   Es otro comportamiento y bastante más voluminoso; si se quiere medir, agregar
   un tercer Tipo_Caso con  EventCause IN (5,6) AND Tipo_Evento_Siguiente = 3.

   COLUMNAS PARA INTERPRETAR EL CASO
   ---------------------------------
   Segundos_Conectado = atención -> fin (crudo).
   Segundos_Hold      = HoldTime del tramo.
   Segundos_Hablado   = Conectado - Hold. Es el que dispara el umbral de 5 s,
                        para que "atiendo, pongo en espera 35 s y transfiero"
                        cuente como gestión nula (Detalle_Caso lo aclara).
   Segundos_Ring      = asignación -> atención (o -> fin si nunca atendió).

   UMBRAL
   ------
   Los 5 segundos están fijos en el WHERE (una vista no toma parámetros), pero
   Segundos_Hablado se expone en el resultado: para mover el corte alcanza con
   filtrar la vista (p. ej. WHERE Segundos_Hablado < 10).

   TIPOS
   -----
   GlobalId y Agente_Legajo salen VARCHAR(50), igual que en
   orion.Silver_Llamadas_Detalle, para poder cruzarlas sin CAST. Ojo con
   GlobalId: son 18 dígitos, no entra en un double — cualquier capa que lo
   serialice como número (JSON, Excel) lo redondea y deja de matchear.

   COSTO
   -----
   El OPENQUERY resuelve las window functions sobre TODA
   omnicanalidad.eventdetailrecords del lado de MySQL (~1M filas, ~25 s) y
   devuelve solo los tramos marcados (~2.6k al 2026-07). SQL Server no puede
   empujar filtros adentro del OPENQUERY: filtrar por fecha en el SELECT de
   afuera acota el resultado pero NO el tiempo. Si se necesita uso interactivo
   frecuente, materializar a una tabla orion.Silver_* con un SP por rango de
   fechas, igual que USP_Cargar_Silver_Llamadas_Detalle.

   CÓMO CORRER
   -----------
   Contra la BD Acme, con permisos DDL sobre el schema orion.
   Idempotente (CREATE OR ALTER). No toca datos.
   ============================================================================ */

USE Acme;
GO

CREATE OR ALTER VIEW orion.vw_Llamadas_Rebotadas_Agente AS
SELECT
    CAST(q.global_id AS VARCHAR(50))            AS GlobalId,
    CAST(q.asignacion AS DATE)                  AS Fecha,
    q.ingreso_cola                              AS Fecha_Hora_Ingreso_Cola,
    q.asignacion                                AS Fecha_Hora_Asignacion,
    q.atencion                                  AS Fecha_Hora_Atencion,
    q.fin                                       AS Fecha_Hora_Fin,
    CAST(q.cola_id AS INT)                      AS Cola_Id,
    q.cola_nombre                               AS Cola_Nombre,
    CAST(q.agente_nro AS VARCHAR(50))           AS Agente_Legajo,
    q.agente_nombre                             AS Agente_Nombre,
    q.numero_cliente                            AS Numero_Cliente,
    q.tipo_caso                                 AS Tipo_Caso,
    q.detalle_caso                              AS Detalle_Caso,
    q.intento                                   AS Intento_Nro,
    q.seg_ring                                  AS Segundos_Ring,
    q.seg_conectado                             AS Segundos_Conectado,
    q.seg_hold                                  AS Segundos_Hold,
    q.seg_hablado                               AS Segundos_Hablado,
    CAST(q.vuelve_cola AS BIT)                  AS Vuelve_A_Cola,
    q.event_cause                               AS Motivo_Corte_Id,
    q.motivo_corte                              AS Motivo_Corte,
    q.sig_tipo                                  AS Tipo_Evento_Siguiente
FROM OPENQUERY([MYSQL_LINK], '
WITH ev AS (
    SELECT
        e.ID, e.GlobalId, e.EventType, e.EventCause,
        e.EventStartTime, e.EventAnswerTime, e.EventEndTime,
        e.Origin, e.Destination, e.Agente, e.HoldTime,
        LAG(e.EventType)      OVER w AS prev_tipo,
        LAG(e.Origin)         OVER w AS prev_origin,
        LAG(e.EventStartTime) OVER w AS prev_inicio,
        LEAD(e.EventType)     OVER w AS nxt_tipo,
        LEAD(e.EventType, 2)  OVER w AS nxt2_tipo
    FROM omnicanalidad.eventdetailrecords e
    WINDOW w AS (PARTITION BY e.GlobalId ORDER BY e.EventStartTime, e.ID)
),
legs AS (
    SELECT
        ev.*,
        CAST(IF(CAST(ev.EventAnswerTime AS CHAR) = ''0000-00-00 00:00:00'', 1, 0) AS SIGNED) AS sin_atender,
        CAST(IF(ev.nxt_tipo = 1 OR (ev.nxt_tipo IN (5, 6) AND ev.nxt2_tipo = 1), 1, 0) AS SIGNED) AS vuelve_cola,
        CAST(ROW_NUMBER() OVER (PARTITION BY ev.GlobalId ORDER BY ev.EventStartTime, ev.ID) AS SIGNED) AS intento,
        CAST(IFNULL(ev.HoldTime, 0) AS SIGNED) AS seg_hold,
        CAST(IF(CAST(ev.EventAnswerTime AS CHAR) = ''0000-00-00 00:00:00'', 0,
                GREATEST(TIMESTAMPDIFF(SECOND, ev.EventAnswerTime, ev.EventEndTime), 0)) AS SIGNED) AS seg_conectado,
        CAST(IF(CAST(ev.EventAnswerTime AS CHAR) = ''0000-00-00 00:00:00'', 0,
                GREATEST(TIMESTAMPDIFF(SECOND, ev.EventAnswerTime, ev.EventEndTime) - IFNULL(ev.HoldTime, 0), 0)) AS SIGNED) AS seg_hablado
    FROM ev
    WHERE ev.EventType = 2      /* tramo de agente ... */
      AND ev.prev_tipo = 1      /* ... que viene de la cola (descarta salientes) */
)
SELECT
    CAST(l.GlobalId AS CHAR) AS global_id,
    l.prev_inicio            AS ingreso_cola,
    l.EventStartTime         AS asignacion,
    IF(l.sin_atender = 1, NULL, l.EventAnswerTime) AS atencion,
    l.EventEndTime           AS fin,
    CAST(l.prev_origin AS SIGNED) AS cola_id,
    CAST(IFNULL(ca.Nombre, CONCAT(''Cola '', l.prev_origin)) AS CHAR) AS cola_nombre,
    CAST(IF(l.Agente IS NULL OR l.Agente = 0, l.Destination, l.Agente) AS CHAR) AS agente_nro,
    CAST(IFNULL(u.NombreUsuario, ''(sin nomina)'') AS CHAR) AS agente_nombre,
    CAST(l.Origin AS CHAR)   AS numero_cliente,
    CAST(IF(l.sin_atender = 1, ''SIN ATENDER'', ''ATENDIDA CORTA'') AS CHAR) AS tipo_caso,
    CAST(CASE
            WHEN l.sin_atender = 1 AND l.vuelve_cola = 1 THEN ''NO ATIENDE Y VUELVE A LA COLA''
            WHEN l.sin_atender = 1                       THEN ''NO ATIENDE Y LA DERIVA''
            WHEN l.seg_hold >= 5                         THEN ''ATIENDE, PONE EN ESPERA Y TRANSFIERE''
            WHEN l.vuelve_cola = 1                       THEN ''ATIENDE Y DEVUELVE A LA COLA''
            ELSE                                              ''ATIENDE Y TRANSFIERE''
         END AS CHAR) AS detalle_caso,
    l.intento,
    CAST(TIMESTAMPDIFF(SECOND, l.EventStartTime,
                       IF(l.sin_atender = 1, l.EventEndTime, l.EventAnswerTime)) AS SIGNED) AS seg_ring,
    l.seg_conectado,
    l.seg_hold,
    l.seg_hablado,
    l.vuelve_cola,
    CAST(l.EventCause AS SIGNED) AS event_cause,
    CAST(IFNULL(cc.Nombre, ''-'') AS CHAR) AS motivo_corte,
    CAST(IFNULL(l.nxt_tipo, 0) AS SIGNED)  AS sig_tipo
FROM legs l
LEFT JOIN omnicanalidad.colas_acd        ca ON ca.IdCola   = CAST(l.prev_origin AS UNSIGNED)
LEFT JOIN omnicanalidad.usuarios         u  ON u.NroAgente = l.Agente
LEFT JOIN omnicanalidad.completioncauses cc ON cc.CauseID  = l.EventCause
WHERE
    /* A) nunca atendió y se la sacó de encima */
    (l.sin_atender = 1 AND (l.vuelve_cola = 1 OR l.nxt_tipo IN (5, 6) OR l.EventCause = 113))
    /* B) atendió pero transfirió con < 5 s de conversación real */
 OR (l.sin_atender = 0
     AND l.seg_hablado < 5
     AND (l.EventCause IN (111, 112, 113) OR l.nxt_tipo IN (1, 5, 6)))
') q;
GO
