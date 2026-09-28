/* ============================================================================
   Feature — Planificador: el pool General de Voltara sin piso de cobertura
   Fecha: 2026-09-11 (posterior a 2026-09-10c_planificador_skill_prioritario.sql)
   Autor: equipo Acme

   QUE CAMBIA
   ----------
   Un solo dato, sin tocar estructura:

       planificacion.Pool.MinOperadores del pool General (campana 20):  2 -> 0

   Los pools inactivos (Reclamos y Grandes Cuentas, Comercial Consumo) conservan
   su 1: mientras esten inactivos no participan del dimensionamiento.

   ============================================================================
   POR QUE
   ============================================================================

   Decision de la operacion (Ignacio, 2026-09-11). En la madrugada el piso de 2
   operadores en linea era lo unico que fijaba la dotacion, y la cadena de
   descuentos lo convertia en 3 personas a citar:

       2 en linea / disponibilidad 0,900 / (1 - 0,082) / (1 - 0,0833) = 2,64 -> 3

   El 10/09 entre las 02:00 y las 04:30 entraron 1 o 2 llamadas por media hora,
   el NDS fue 100% con 2 personas con turno, y el cotejo mostraba "hacia falta 3".
   Sin el piso, el nivel de servicio pide 1 en linea -> 1,32 -> 2 a citar.

   ============================================================================
   CUANTO MUEVE (medido antes de aplicar)
   ============================================================================

   Plan vigente (corrida 64, 30 dias, 1.440 intervalos):
       28.437 -> 28.431 intervalos-operador; cambian 6 intervalos.

   Cotejo con la demanda real, 25/08 al 10/09 (17 dias):
       hacia falta 13.999 -> 13.913; contra tenian turno +0,52% -> -0,10%.

   OJO: con el piso en 0, un intervalo con CERO llamadas pronosticadas planifica
   0 personas (motivo "sin llamadas"). Hoy no hay ninguno en el horizonte: con
   cualquier llamada pronosticada Erlang ya pide al menos 1 operador.

   DESPUES DE CORRERLA: recalcular el plan desde la pagina para que la corrida
   vigente lo tome.

   Idempotente: el UPDATE solo toca la fila si todavia no esta en 0.
   ============================================================================ */

SET NOCOUNT ON;
GO

IF OBJECT_ID('planificacion.Pool', 'U') IS NOT NULL
BEGIN
    UPDATE planificacion.Pool
    SET MinOperadores = 0
    WHERE CampanaID = 20
      AND Nombre = N'General'
      AND MinOperadores <> 0;

    PRINT CONCAT('Pool General de Voltara sin piso de cobertura (filas: ', @@ROWCOUNT, ').');
END
GO
