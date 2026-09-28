/* ============================================================================
   Cambio de schema — Planificador: rinde de la gente nueva (antigüedad)
   Fecha: 2026-09-15 (posterior a 2026-09-15b_planificador_perfil_presencia.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   1. Crea planificacion.CurvaAntiguedad: por tramo de dias desde el pase a piso,
      cuanto rinde una persona contra un antiguo en las mismas colas (Rinde) y
      cuanto pesa en la malla (Factor, normalizado contra la mezcla de antiguedades
      con la que se midio el TMO del pronostico).
   2. Agrega planificacion.Requerimiento.CitadosEquivalentes: los citados de cada
      intervalo contados con ese peso. La brecha se calcula sobre ellos.
   No siembra nada: la carga scripts/planificador_antiguedad.py (cron semanal,
   antes del recalculo automatico). Sin filas, cada persona pesa 1 y el plan
   queda exactamente como antes.

   POR QUE
   -------
   Ignacio (2026-09-15) pidio el descuento por gente nueva. Medido sobre Voltara
   (jun-sep 2026, detalle de llamadas cruzado por nombre con la nomina), la
   gente nueva NO se conecta menos: en sus primeras semanas esta logueada mas
   tiempo de su turno que los antiguos. Lo que cambia es el TMO, y comparado
   dentro de la misma cola: en EMERGENCIAS 261 s en las semanas 1-2, 220 s en
   las 3-4 y 207 s en las 5-8 contra 176 s de un antiguo; en COMERCIAL 446,
   386 y 342 contra 329. La malla los contaba enteros.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.CurvaAntiguedad', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.CurvaAntiguedad (
        CampanaID      INT          NOT NULL,
        -- Dias desde el pase a piso (nomina.fecha_piso) en que arranca el tramo.
        DiaDesde       SMALLINT     NOT NULL,
        -- Donde termina (exclusivo). NULL = en adelante: los antiguos.
        DiaHasta       SMALLINT     NULL,
        -- Contra un antiguo en las mismas colas, ya encogido por llamadas.
        Rinde          DECIMAL(5,4) NOT NULL,
        -- Lo que pesa una persona del tramo al contar la malla.
        Factor         DECIMAL(5,4) NOT NULL,
        -- Llamadas con las que se midio el rinde del tramo.
        Llamadas       INT          NOT NULL,
        -- Parte de las llamadas de la historia del TMO que atendio el tramo.
        Participacion  DECIMAL(5,4) NULL,
        MedidoEn       DATETIME2(0) NOT NULL
            CONSTRAINT DF_Plan_CurvaAntiguedad_MedidoEn DEFAULT SYSDATETIME(),

        CONSTRAINT PK_Plan_CurvaAntiguedad PRIMARY KEY (CampanaID, DiaDesde),
        CONSTRAINT CK_Plan_CurvaAntiguedad_Desde CHECK (DiaDesde >= 0),
        CONSTRAINT CK_Plan_CurvaAntiguedad_Hasta CHECK (DiaHasta IS NULL OR DiaHasta > DiaDesde),
        CONSTRAINT CK_Plan_CurvaAntiguedad_Rinde CHECK (Rinde > 0 AND Rinde <= 2),
        CONSTRAINT CK_Plan_CurvaAntiguedad_Factor CHECK (Factor > 0 AND Factor <= 2),
        CONSTRAINT CK_Plan_CurvaAntiguedad_Part CHECK (Participacion IS NULL OR Participacion BETWEEN 0 AND 1)
    );
    PRINT 'planificacion.CurvaAntiguedad creada.';
END
ELSE PRINT 'planificacion.CurvaAntiguedad ya existia.';
GO

IF COL_LENGTH('planificacion.Requerimiento', 'CitadosEquivalentes') IS NULL
BEGIN
    ALTER TABLE planificacion.Requerimiento ADD CitadosEquivalentes DECIMAL(7,2) NULL;
    PRINT 'planificacion.Requerimiento.CitadosEquivalentes agregada.';
END
ELSE PRINT 'planificacion.Requerimiento.CitadosEquivalentes ya existia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, DiaDesde, DiaHasta, Rinde, Factor, Llamadas, Participacion, MedidoEn
FROM planificacion.CurvaAntiguedad
ORDER BY CampanaID, DiaDesde;

SELECT COL_LENGTH('planificacion.Requerimiento', 'CitadosEquivalentes') AS bytes_columna_nueva;
GO
