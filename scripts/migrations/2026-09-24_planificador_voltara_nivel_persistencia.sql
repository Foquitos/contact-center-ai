/* ============================================================================
   Datos — Planificador: prender en Voltara el GBDT del nivel diario, la
   persistencia del desvío de ayer y el reescalado intradía
   Fecha: 2026-09-24 (posterior a 2026-09-22_planificador_hidra_tecnico.sql,
   que agregó las columnas de persistencia e intradía)
   Autor: equipo Acme

   POR QUÉ
   -------
   Backtest de un año (2025-09-24 a 2026-09-23) con el código de producción,
   demanda del cliente, MAPE del total diario / WAPE por media hora:

                                   plan de la mañana     a 1 día        a 7 días
     hoy (sin nada de esto)          17,7% / 22,3%    18,3% / 22,6%   18,8% / 22,9%
     + GBDT del nivel (peso 0,4)     16,0% / 21,2%    16,9% / 21,9%   17,7% / 22,4%
     + persistencia 0,45/0,15/3      14,4% / 20,2%    16,7% / 21,8%   17,7% / 22,4%

   Gana los cuatro tipos de día (plan de la mañana: hábil 12,8 -> 10,4, sábado
   26,4 -> 21,6, domingo 27,9 -> 23,5, feriado 28,9 -> 22,4) y todos los
   trimestres. El sesgo queda en -4,9% (por debajo), del lado que conviene.

   Los pesos de la persistencia NO son los de Hidra: salen de regresar el desvío
   de cada día sobre el de k días antes (Voltara, plan de la mañana con GBDT):
   k=1 0,44 · k=2 0,14 · k=3 0,07 · k>=4 ruido (cero en la primera mitad del
   año). Con los de Hidra (0,53/0,35/7) el plan de la mañana da igual y el de un
   día empeora en feriados.

   El intradía (desde las 14, o sea el recálculo de las 14:00) reescala lo que
   queda del día por cómo vienen NUESTRAS llamadas: error del resto del día
   35,9% -> 34,7%. Desde las 12 no gana y desde las 10 empeora. Necesita el
   cambio de código que lo habilita para Voltara (planificador_servicio: antes
   se salteaba con la demanda del cliente); sin ese código, esta columna no
   hace nada.

   Sólo toca la campaña 20 y sólo si sigue como estaba (GBDT apagado y
   persistencia en cero): si alguien ya lo cambió desde la pantalla, no lo pisa.
   Después, recalcular el plan de Voltara (o esperar al cron).
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'PersistenciaPesoHoy') IS NULL
    RAISERROR('Falta aplicar 2026-09-22_planificador_hidra_tecnico.sql (columnas nuevas).', 16, 1);
ELSE
BEGIN
    UPDATE planificacion.Campana
    SET NivelGbdt = 1,
        NivelGbdtPeso = 0.400,
        PersistenciaPesoHoy = 0.450,
        PersistenciaPesoResto = 0.150,
        PersistenciaDias = 3,
        IntradiaDesdeHora = 14,
        ActualizadoEn = SYSUTCDATETIME()
    WHERE CampanaID = 20
      AND NivelGbdt = 0
      AND PersistenciaPesoHoy = 0 AND PersistenciaPesoResto = 0;
    PRINT CONCAT('Voltara: ', @@ROWCOUNT, ' fila actualizada.');
END
GO

SELECT CampanaID, NivelGbdt, NivelGbdtPeso, PersistenciaPesoHoy,
       PersistenciaPesoResto, PersistenciaDias, IntradiaDesdeHora
FROM planificacion.Campana WHERE CampanaID = 20;
GO
