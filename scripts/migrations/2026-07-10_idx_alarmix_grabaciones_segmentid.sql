/* ============================================================================
   Performance — Índice sobre ALARMIX.Grabaciones(segmentId)
   Fecha: 2026-07-10
   Autor: equipo Acme

   POR QUÉ
   -------
   El dashboard de auditorías (bandeja) enriquece las auditorías de campañas de
   origen ALARMIX/Genesys resolviendo el EQUIPO REAL (supervisor de Acme) y el nombre
   del agente. Para eso cruza cada auditoría por
       calidad.Auditorias.IdAplicativo = ALARMIX.Grabaciones.segmentId
   y desde la grabación toma `agentIds` (que sí matchea usuarios.usuario) para
   resolver nomina -> operadores -> equipos.

   Hoy [Acme].[ALARMIX].[Grabaciones] es un HEAP sin ningún índice (~456k filas), así
   que cada lookup por segmentId es un scan completo. Este índice convierte ese
   cruce en un seek. Los INCLUDE cubren las dos columnas que lee el enriquecido
   (agentIds y agentName_sort) para que sea un índice cubriente.

   NOTAS
   -----
   · segmentId NO es único en la tabla (puede haber varios segmentos por llamada),
     por eso es un índice NONCLUSTERED común, no UNIQUE.
   · Solo mejora performance; no cambia datos ni resultados.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema ALARMIX.
   Idempotente: se crea solo si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_ALARMIX_Grabaciones_segmentId'
      AND object_id = OBJECT_ID('ALARMIX.Grabaciones')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_ALARMIX_Grabaciones_segmentId
        ON [Acme].[ALARMIX].[Grabaciones] (segmentId)
        INCLUDE (agentIds, agentName_sort);
END
GO
