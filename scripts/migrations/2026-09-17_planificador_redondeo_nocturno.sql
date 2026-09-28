/* ============================================================================
   Cambio de schema y datos — Planificador: redondear para abajo de madrugada
   Fecha: 2026-09-17 (posterior a 2026-09-16b_planificador_t1_consumo_dedicada.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Agrega a planificacion.Campana una franja horaria en la que la gente a citar
   se redondea PARA ABAJO en vez de para arriba:
       RedondeoAbajoDesde   hora de inicio (0-23), inclusive
       RedondeoAbajoHasta   hora de fin (1-24), exclusiva
   Las dos en NULL = apagado (se redondea para arriba todo el dia, como siempre).
   Si Desde > Hasta la franja cruza la medianoche (22 a 6).
   Siembra Voltara con 0 a 8.

   POR QUE
   -------
   Ignacio (2026-09-17): de 00 a 08 el planificador pide mas gente por una
   ganancia minima. De madrugada la cola necesita 1 a 3 operadores en linea y la
   cadena (disponibilidad, ausentismo, break) le suma menos de una persona:
   2 en linea -> 2,5 -> 3 a citar. Redondear para abajo cita exactamente los que
   tienen que estar en la linea. Nunca menos: la cadena solo divide por factores
   menores a 1, asi que para abajo nunca queda por debajo de "en linea".

   MEDIDO, plan vigente (corrida 91, 30 dias): de 00 a 08, 1.747 intervalos-
   operador para arriba contra 1.267 para abajo = 240 h menos (2% del plan).
   Lo que paso de madrugada, 17/08-16/09, habiles: 2,3-2,6 con turno de 01 a 05,
   1,3-1,9 telefonicos en linea, NDS 86-93% y abandono 2,6-6,1%.

   SE PUEDE CAMBIAR desde Configuracion (vaciar las dos horas lo apaga). Hay que
   RECALCULAR EL PLAN despues de aplicarla.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'RedondeoAbajoDesde') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        RedondeoAbajoDesde TINYINT NULL,
        RedondeoAbajoHasta TINYINT NULL;
    PRINT 'RedondeoAbajoDesde y RedondeoAbajoHasta agregados a planificacion.Campana.';
END
ELSE PRINT 'planificacion.Campana ya tenia las columnas de redondeo.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Campana_RedondeoAbajo')
BEGIN
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_RedondeoAbajo
        CHECK ((RedondeoAbajoDesde IS NULL AND RedondeoAbajoHasta IS NULL)
               OR (RedondeoAbajoDesde BETWEEN 0 AND 23
                   AND RedondeoAbajoHasta BETWEEN 1 AND 24
                   AND RedondeoAbajoDesde <> RedondeoAbajoHasta));
    PRINT 'CK_Plan_Campana_RedondeoAbajo creado.';
END
ELSE PRINT 'CK_Plan_Campana_RedondeoAbajo ya existia.';
GO

/* Solo si esta apagada: re-correrla no pisa otra franja puesta a mano (pero si
   alguien la apago vaciando las dos horas, la vuelve a prender). */
UPDATE planificacion.Campana
SET RedondeoAbajoDesde = 0, RedondeoAbajoHasta = 8
WHERE CampanaID = 20
  AND RedondeoAbajoDesde IS NULL AND RedondeoAbajoHasta IS NULL;
PRINT CONCAT('Voltara, redondeo para abajo de 0 a 8: ', @@ROWCOUNT, ' fila(s).');
GO
