/* ============================================================================
   Datos — Planificador: alta de Hidra Técnico (Mitrol, campaña HIDRAIN)
   Fecha: 2026-09-22 (posterior a 2026-09-17_planificador_redondeo_nocturno.sql)
   Autor: equipo Acme

   QUÉ HACE
   --------
   1. Agrega seis columnas a planificacion.Campana (calendario propio,
      persistencia del desvío y reescalado intradía). Nacen APAGADAS: Voltara
      queda exactamente como estaba.
   2. Da de alta en el planificador la campaña 1 de calidad.Campanas ("HidraIN",
      empresa HIDRA), que es Hidra Técnico, con esas tres cosas prendidas. No toca
      Voltara ni asigna permisos:

     Campana          CampanaID 1, con paciencia, shrinkage y clima medidos
     Pool             'Técnico', un solo pool
     Skill            481 = idCampania de HIDRAIN en Mitrol (la cola es la campaña)
     PoolOrigen       sub-campaña de RRHH 57 (Hidra Canal Telefónico)
     Disponibilidad   por hora, medida sobre 90 días

   El código que lee sus llamadas (FuenteHidraTecnico en planificador_datos.py)
   tiene que estar deployado ANTES o junto: sin él la campaña aparece en el
   selector como "sin fuente de datos" y los crons la saltean, sin romper nada.

   POR QUÉ ASÍ
   -----------
   Hidra son dos campañas que funcionan como dos empresas: Técnico atiende por
   Mitrol y Comercial (campaña 24) por Hermes, con operadores que no se pasan de
   una a otra. Por eso son dos campañas del planificador y no dos pools de una.
   Ésta es sólo la de Mitrol; Comercial todavía no tiene fuente.

   LO MEDIDO (2026-09-22, sin escribir nada: backtest y calibración con la
   configuración armada en memoria)
   -----------------------------------------------------------------------------
   - Volumen: ~2.000-2.500 llamadas por día hábil, ~500-1.000 el fin de semana,
     TMO ~180 s. Semana del 14/09: 12.328 entrantes, 80,3% atendidas en 20 s,
     1,6% de abandono.
   - Paciencia: Kaplan-Meier sobre 365 días (534.881 llamadas, 41.213
     abandonos) ajustada a 60 s = 2.482 s. A los 60 s sigue esperando el 97,6%
     y a los 300 s el 73,4%: gente muy paciente (reclamos de agua).
   - Shrinkage de nómina (dbo.payroll, 90 días, sub-campaña 57): 2,4% =
     1,3% ausentismo + 1,1% faltante dentro del turno; capacitación 0,5%.
     Cierra contra los conectados: la semana del 14/09 hubo 1.094,8 h de turno
     y 1.062,7 h logueadas de esa gente en HIDRAIN (97%).
   - Disponibilidad: por hora; donde la cola no ata (Erlang explica menos del
     servicio que se logró) el factor queda en 1, igual que en Voltara.
   - Pronóstico: backtest sobre un año corrido (2025-09-22 a 2026-09-20,
     antelación 7), contra el dbo.Forecast de Hidra que ya usa la operación:
         WAPE media hora   24,5%  contra 29,6%
         MAPE diario       17,2%  contra 23,5%  (hábil 15,8/20,9 · sábado
                                                22,0/30,7 · domingo 19,2/28,8)
     Sesgo −9%: el volumen viene subiendo y la corrección de nivel llega tarde.
   - Lo que se prende para Hidra (mismo año; MAPE diario / WAPE media hora):
         plan de la mañana (antelación 0)   16,3% / 24,0%  ->  13,3% / 21,6%
         plan de mañana    (antelación 1)   16,7% / 24,1%  ->  15,0% / 22,4%
         a 7 días                           17,2% / 24,5%  ->  17,1% / 24,4%
       · Persistencia (0,53 hoy, 0,35 los 6 días siguientes): el desvío de un
         día correlaciona 0,53 con el del siguiente; los eventos duran 2-3 días.
       · Puente = 0,6 de su día hábil (error de los puentes 55% -> 32%) y feriado
         hábil con el perfil del sábado (feriados 25% -> 22% con persistencia).
       · Reescalado del resto del día desde las 12 (22,5% -> 20,9%; el cron de
         las 14 lo aplica).
     Lo que queda: los CORTES PROGRAMADOS de Hidra (tipificación 'Corte
     programado'; 14/08/2026: 1.577 de 4.563 llamadas). Sin la fecha del corte
     de antemano no se pueden anticipar; la persistencia agarra el segundo día.
     Si la operación se entera antes, se cargan como Ajuste (factor sobre esas
     horas) desde la pantalla, como cualquier evento previsto.
     Probado y descartado: clima (R² ≈ 0,10 con los rasgos de Voltara; MAPE 17,6%,
     y en ago-sep 2026 empeora 15,1% → 21,4%) y ventana de nivel de 14 días
     (17,5%). Por eso ClimaActivo nace en 0 y la ventana queda en 52 semanas / 28
     días, como Voltara.

   A CONFIRMAR POR LA OPERACIÓN
   -----------------------------
   - Objetivo de nivel de servicio: se siembra 80% en 30 s porque es el umbral
     con el que Mitrol tiene configurada HIDRAIN (`TiempoUmbral` = 30 en
     eficiencia_de_campana) y el que usa hoy Voltara en planificacion.Skill. Si
     el contrato de Hidra dice otra cosa, se cambia desde Configuración.
   - Hidra RRSS (97, 5 personas) y Hidra Back Office (109) NO se cargaron como
     refuerzo: no hay evidencia de que pasen al teléfono. Si lo hacen, se suman
     desde Configuración como el Digital de Voltara.

   DESPUÉS DE APLICARLA
   --------------------
   1. Recalcular el plan de Hidra Técnico desde la pantalla (o esperar al cron de
      las 06:00/14:00, que recorre todas las campañas con fuente).
   2. Los crons semanales (perfil de presencia y antigüedad) la toman solos.
   3. El clima nace apagado. Para volver a probarlo en el Laboratorio hace falta
      el histórico: `python scripts/clima_voltara.py --campana 1 --desde 2022-01-01`
      (después clima_diario.sh --todas lo mantiene al día).
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ------------------------------------------------------------------------
   Columnas nuevas de planificacion.Campana (sirven a cualquier campaña; nacen
   apagadas, así que Voltara queda exactamente como estaba):
     FeriadoComoSabado      el feriado usa el perfil del sábado (no el domingo)
     PuenteFactor           NULL = el puente es feriado; con valor, su día hábil x factor
     PersistenciaPesoHoy    peso del desvío de ayer sobre el plan de hoy (0 = off)
     PersistenciaPesoResto  el mismo, para los días siguientes
     PersistenciaDias       hasta cuántos días se aplica
     IntradiaDesdeHora      NULL = off; hora desde la que el recálculo reescala
                            lo que queda del día por lo que ya entró
   ------------------------------------------------------------------------ */
IF COL_LENGTH('planificacion.Campana', 'PersistenciaPesoHoy') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        FeriadoComoSabado     BIT          NOT NULL CONSTRAINT DF_Plan_Campana_FeriadoSabado DEFAULT (0),
        PuenteFactor          DECIMAL(4,3) NULL,
        PersistenciaPesoHoy   DECIMAL(4,3) NOT NULL CONSTRAINT DF_Plan_Campana_PersHoy DEFAULT (0),
        PersistenciaPesoResto DECIMAL(4,3) NOT NULL CONSTRAINT DF_Plan_Campana_PersResto DEFAULT (0),
        PersistenciaDias      TINYINT      NOT NULL CONSTRAINT DF_Plan_Campana_PersDias DEFAULT (0),
        IntradiaDesdeHora     TINYINT      NULL;
    PRINT 'Columnas de calendario, persistencia e intradía agregadas a planificacion.Campana.';
END
ELSE PRINT 'planificacion.Campana ya tenía las columnas de persistencia.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Campana_Persistencia')
BEGIN
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_Persistencia
        CHECK (PersistenciaPesoHoy BETWEEN 0 AND 1
               AND PersistenciaPesoResto BETWEEN 0 AND 1
               AND PersistenciaDias BETWEEN 0 AND 30
               AND (PuenteFactor IS NULL OR PuenteFactor BETWEEN 0.1 AND 1.5)
               AND (IntradiaDesdeHora IS NULL OR IntradiaDesdeHora BETWEEN 6 AND 22));
    PRINT 'CK_Plan_Campana_Persistencia creado.';
END
GO

-- Si la campaña 1 no es la que se cree, no se siembra nada (NOEXEC saltea el resto
-- de los batches; RAISERROR solo cortaría éste).
IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = 1 AND Nombre = 'HidraIN')
BEGIN
    RAISERROR('calidad.Campanas 1 no es HidraIN: revisar antes de sembrar.', 16, 1);
    SET NOEXEC ON;
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Campana WHERE CampanaID = 1)
BEGIN
    INSERT INTO planificacion.Campana
        (CampanaID, Activa, IntervaloMin, MaxOcupacion, ShrinkageDefault, PacienciaSeg, Nota,
         PacienciaHorizonteSeg, PacienciaOrigen,
         ShrinkageAusentismo, ShrinkageCapacitacion, ShrinkageOrigen, ShrinkageMedidoEn,
         SemanasBase, DiasNivelReciente, NivelPorTipoDeDia,
         ClimaLat, ClimaLon, ClimaActivo,
         FeriadoComoSabado, PuenteFactor, PersistenciaPesoHoy, PersistenciaPesoResto,
         PersistenciaDias, IntradiaDesdeHora)
    VALUES
        (1, 1, 30, 0.850, 0.024, 2482,
         N'Hidra Técnico (Mitrol, HIDRAIN). Paciencia por Kaplan-Meier sobre 365 días ajustada a 60 s (a los 60 s sigue esperando el 97,6%). Shrinkage medido sobre dbo.payroll, 90 días: 1,3% ausentismo + 1,1% dentro del turno.',
         60, N'medido',
         0.013, 0.005, N'medido', SYSUTCDATETIME(),
         52, 28, 1,
         -34.610000, -58.440000, 0,
         1, 0.600, 0.530, 0.350, 7, 12);
    PRINT 'Campaña Hidra Técnico sembrada.';
END
ELSE PRINT 'Hidra Técnico ya estaba dada de alta: no se toca.';
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Pool WHERE CampanaID = 1)
BEGIN
    INSERT INTO planificacion.Pool (CampanaID, Nombre, MinOperadores, Nota) VALUES
        (1, N'Técnico', 0,
         N'Una sola cola (HIDRAIN). ~40 operadores de Hidra Canal Telefónico, que sólo se loguean en esta campaña.');
    PRINT 'Pool de Hidra Técnico sembrado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Skill WHERE CampanaID = 1)
BEGIN
    DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 1 AND Nombre = N'Técnico');

    INSERT INTO planificacion.Skill
        (CampanaID, SkillID, Nombre, PoolID, ObjetivoNds, UmbralSeg, MaxAbandono, PacienciaSeg, Nota)
    VALUES
        (1, 481, 'HIDRAIN', @pool, 0.800, 30, NULL, 2482,
         N'idCampania de Mitrol. Umbral de 30 s = TiempoUmbral de Mitrol para HIDRAIN: confirmar contra el contrato.');
    PRINT 'Skill de Hidra Técnico sembrado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.PoolOrigen o
               JOIN planificacion.Pool p ON p.PoolID = o.PoolID
               WHERE p.CampanaID = 1)
BEGIN
    DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 1 AND Nombre = N'Técnico');

    INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota) VALUES
        (@pool, 57, N'Hidra Canal Telefónico. Semana del 14/09/2026: 39 de las 41 personas logueadas en HIDRAIN (la 55 son referentes, puesto 10).');
    PRINT 'Origen de la gente de Hidra Técnico sembrado.';
END
GO

/* Medida el 2026-09-22 sobre 90 días (intervalos donde la cola ató). De 00 a 07
   no hay intervalos con cola suficiente para medir: 1 a mano. */
IF NOT EXISTS (SELECT 1 FROM planificacion.Disponibilidad WHERE CampanaID = 1)
BEGIN
    INSERT INTO planificacion.Disponibilidad
        (CampanaID, PoolID, DiaSemana, HoraDesde, HoraHasta, Factor, Origen, Muestras)
    VALUES
        (1, NULL, 0,  0,  7, 1.000, 'manual', NULL),
        (1, NULL, 0,  7, 10, 1.000, 'medido', 311),
        (1, NULL, 0, 10, 11, 0.941, 'medido', 158),
        (1, NULL, 0, 11, 12, 0.895, 'medido', 156),
        (1, NULL, 0, 12, 13, 0.882, 'medido', 151),
        (1, NULL, 0, 13, 14, 0.917, 'medido', 116),
        (1, NULL, 0, 14, 16, 1.000, 'medido', 257),
        (1, NULL, 0, 16, 17, 0.909, 'medido', 130),
        (1, NULL, 0, 17, 24, 1.000, 'medido', 586);
    PRINT 'Disponibilidad de Hidra Técnico sembrada.';
END
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT c.CampanaID, c.Activa, c.ShrinkageDefault, c.PacienciaSeg, c.ClimaActivo,
       p.Nombre AS Pool, s.SkillID, s.Nombre AS Skill, s.ObjetivoNds, s.UmbralSeg,
       o.CampanaRRHHID
FROM planificacion.Campana c
JOIN planificacion.Pool p ON p.CampanaID = c.CampanaID
JOIN planificacion.Skill s ON s.CampanaID = c.CampanaID AND s.PoolID = p.PoolID
LEFT JOIN planificacion.PoolOrigen o ON o.PoolID = p.PoolID
WHERE c.CampanaID = 1;
GO

SET NOEXEC OFF;
GO
