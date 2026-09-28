/*
  2026-07-22 — Backfill de "Motivo de finalización" en las auditorías de Vantix
  que quedaron guardadas como 'Sin determinar'.

  POR QUÉ QUEDARON ASÍ
  --------------------
  La columna sale del EventCause del tramo del agente en la base MySQL de Orion
  (omnicanalidad.eventdetailrecords, ver SQL_query.py::get_filtered_data_Vantix).
  Cuando la llamada entra por IVR, Orion escribe el tramo del agente con
  EventCause = 0 ("Desconocida") y la causa fina no queda en ningún lado: pasa en
  ~16% de las llamadas auditables, y en la primera tanda de auditorías fue el 39%.

  ContactCenter sí distingue quién cortó en esos casos (EstadPorLlamada.MotivoCorte:
  5 = "Agente corta", 6 = "Desconexión"). Sobre 8 días de llamadas auditables las dos
  fuentes coinciden en el 99.8% cuando ambas tienen dato ("Agente corta" <->
  Desconexión Local 7305 vs 12 discrepancias; "Desconexión" <-> Desconexión 2946 sin
  discrepancias), así que el respaldo es sólido.

  El builder ya aplica este respaldo para las auditorías nuevas. Esto corrige las que
  se guardaron antes de ese cambio.

  ALCANCE
  -------
  Solo toca filas de Vantix (EmpresaID = 5) cuyo extras dice exactamente
  'Sin determinar', y solo cuando ContactCenter da un motivo concluyente (5 o 6).
  El resto del JSON de extras queda intacto (JSON_MODIFY reemplaza una sola clave).

  Las llamadas que ya salieron de la retención de EstadPorLlamada en Orion no van a
  matchear y se quedan en 'Sin determinar': no hay de dónde recuperarlas.

  IDEMPOTENTE: al correr de nuevo no encuentra filas (ya no dicen 'Sin determinar').
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

WITH cc AS (
    SELECT CAST(ID_Llamada AS VARCHAR(50)) COLLATE DATABASE_DEFAULT AS IdLlamada,
           MotivoCorte
    FROM OPENQUERY(ORION_LINK, '
        SELECT e.ID_Llamada, e.MotivoCorte
        FROM ContactCenter.dbo.EstadPorLlamada e
        WHERE e.Fecha >= DATEADD(DAY, -90, GETDATE())
          AND e.Agente <> 0
          AND e.MotivoCorte IN (5, 6)')
)
UPDATE a
SET extras = JSON_MODIFY(
        a.extras,
        '$."Motivo de finalización"',
        CASE cc.MotivoCorte
            WHEN 5 THEN N'Cortó el operador'
            WHEN 6 THEN N'Cortó el cliente'
        END)
FROM calidad.Auditorias a
JOIN cc ON cc.IdLlamada = a.IdAplicativo COLLATE DATABASE_DEFAULT
WHERE a.EmpresaID = 5
  AND ISJSON(a.extras) = 1
  AND JSON_VALUE(a.extras, '$."Motivo de finalización"') = N'Sin determinar';

PRINT CONCAT('Auditorías corregidas: ', @@ROWCOUNT);

COMMIT;

/*
  VERIFICACIÓN (correr después; no debería quedar ninguna 'Sin determinar' de los
  últimos 90 días):

    SELECT JSON_VALUE(extras, '$."Motivo de finalización"') AS Motivo, COUNT(*) AS Cant
    FROM calidad.Auditorias
    WHERE EmpresaID = 5 AND ISJSON(extras) = 1
      AND JSON_VALUE(extras, '$."Motivo de finalización"') IS NOT NULL
    GROUP BY JSON_VALUE(extras, '$."Motivo de finalización"')
    ORDER BY Cant DESC;
*/
