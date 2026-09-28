/* ============================================================================
   Datos — Planificador: prender en Hidra Técnico el calendario propio, la
   persistencia y el reescalado intradía
   Fecha: 2026-09-23 (posterior a 2026-09-22_planificador_hidra_tecnico.sql)
   Autor: equipo Acme

   POR QUÉ HACE FALTA
   ------------------
   La 2026-09-22 se aplicó dos veces: la primera versión dio de alta Hidra
   Técnico (campaña 1) y la segunda agregó las seis columnas nuevas, pero como la
   campaña ya existía su INSERT no corrió y quedó con todo apagado (verificado
   el 2026-09-23: PersistenciaPesoHoy 0, PuenteFactor NULL, IntradiaDesdeHora
   NULL). Esto deja los valores medidos (ver el encabezado de la 2026-09-22):

     FeriadoComoSabado      1      feriado hábil con el perfil del sábado
     PuenteFactor           0,600  puente = su día hábil x 0,6
     PersistenciaPesoHoy    0,530  desvío de ayer sobre el plan de hoy
     PersistenciaPesoResto  0,350  y sobre los 6 días siguientes
     PersistenciaDias       7
     IntradiaDesdeHora      12     el recálculo de las 14 reescala el resto del día

   Sobre un año: plan de la mañana 16,3% -> 13,2% de error diario.

   Sólo toca la campaña 1 y sólo si sigue con la persistencia en cero: si alguien
   ya la cambió desde la pantalla, no la pisa. Después, recalcular el plan de
   Hidra (o esperar al cron de las 14:00).
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'PersistenciaPesoHoy') IS NULL
    RAISERROR('Falta aplicar 2026-09-22_planificador_hidra_tecnico.sql (columnas nuevas).', 16, 1);
ELSE
BEGIN
    UPDATE planificacion.Campana
    SET FeriadoComoSabado = 1,
        PuenteFactor = 0.600,
        PersistenciaPesoHoy = 0.530,
        PersistenciaPesoResto = 0.350,
        PersistenciaDias = 7,
        IntradiaDesdeHora = 12,
        ActualizadoEn = SYSUTCDATETIME()
    WHERE CampanaID = 1
      AND PersistenciaPesoHoy = 0 AND PersistenciaPesoResto = 0;
    PRINT CONCAT('Hidra Técnico: ', @@ROWCOUNT, ' fila actualizada.');
END
GO

SELECT CampanaID, FeriadoComoSabado, PuenteFactor, PersistenciaPesoHoy,
       PersistenciaPesoResto, PersistenciaDias, IntradiaDesdeHora
FROM planificacion.Campana WHERE CampanaID = 1;
GO
