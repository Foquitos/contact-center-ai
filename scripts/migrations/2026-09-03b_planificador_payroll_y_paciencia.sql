/* ============================================================================
   Feature — Planificador: dotación planificada real, ausentismo medido y
             paciencia estimada sin suponer forma
   Fecha: 2026-09-03 (posterior a 2026-09-03_planificador.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1. planificacion.PoolOrigen    — qué sub-campañas de RRHH alimentan cada pool.
   2. planificacion.CodigoPayroll — qué significa cada código de `dbo.payroll`.
   3. Columnas nuevas en Campana (calibración de la paciencia y desglose del
      shrinkage) y en Requerimiento (dotación planificada, para comparar).

   POR QUÉ — TRES COSAS QUE NO SE ENTENDÍAN
   -----------------------------------------

   (a) DE DÓNDE SALE EL AUSENTISMO. Antes era un 0,300 escrito a mano. Ahora sale
   de `dbo.payroll`, que es donde RRHH guarda lo que estaba planificado y lo que
   pasó. Medido sobre las sub-campañas telefónicas de Voltara, jun-ago 2026:

       horas programadas totales        37.908 h
       trabajadas en la cola (P/PRES)   27.604 h   (72,8%)
       ausentismo (ABS y licencias)      6.464 h   (17,1%)
       capacitación (CAPA/CAPAEX)        3.531 h   ( 9,3%)
       => shrinkage de nómina           27,2%

   O sea que el 0,300 que había puesto de arranque era razonable, pero ahora es un
   número medido, desagregado y recalculable, y no una suposición. `CodigoPayroll`
   es lo que hace que la cuenta sea auditable: dice qué código cuenta como qué, y
   se corrige sin tocar código.

   (b) POR QUÉ EL POOL NO CUADRABA CON LA GENTE. Los operadores que atienden el
   teléfono de Voltara pertenecen a **14 sub-campañas distintas** de RRHH, no solo a
   las telefónicas: en agosto de 2026 atendieron llamadas gente de "T1 - Teléfono"
   (58), pero también de "Digital" (13), "Backoffice RRSS" (8), "BackOffice
   Electro" (7), "Artefactos Dañados" (6) y varias más. Filtrar por las que
   *parecen* telefónicas daba 42 planificados en el pico contra 57 personas
   realmente atendiendo. Por eso el mapeo va en una tabla y se siembra con lo que
   dicen los datos, no con lo que parece.

   (c) LA PACIENCIA. `Campana.PacienciaSeg` estaba estimada con el MLE exponencial
   sobre toda la serie, y eso pesa demasiado la cola larga. Medida con
   Kaplan-Meier (sin suponer forma) sobre jun-ago 2026, Emergencias:

       a los  20s sigue esperando el 98,8%
       a los  60s sigue esperando el 94,8%
       a los 120s sigue esperando el 86,3%
       a los 300s sigue esperando el 61,1%

   Los clientes de Voltara son MUY pacientes —es una línea de emergencias de luz, no
   una consulta de saldo—, pero el número que hay que usar para dimensionar no es
   la media global sino el que reproduce el abandono en el rango donde realmente se
   espera. Ajustada a los primeros 60 segundos da 1.126s en vez de 787s.
   `PacienciaHorizonteSeg` guarda ese horizonte de ajuste.

   OJO CON LOS 600 SEGUNDOS: a los 10 minutos hay 207 abandonos contra 35 atenciones,
   un salto que no parece paciencia sino un corte del sistema. La estimación se
   ajusta al rango corto justamente para no comerse ese artefacto.

   CÓMO CORRER
   -----------
   Después de 2026-09-03_planificador.sql. Idempotente y aditiva.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ------------------------------------------------- origen de gente del pool */
IF OBJECT_ID('planificacion.PoolOrigen', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.PoolOrigen (
        PoolID         INT      NOT NULL,
        -- dbo.campanas.id (el catálogo de RRHH, que NO es calidad.Campanas).
        CampanaRRHHID  SMALLINT NOT NULL,
        Nota           NVARCHAR(200) NULL,

        CONSTRAINT PK_Plan_PoolOrigen PRIMARY KEY (PoolID, CampanaRRHHID),
        CONSTRAINT FK_Plan_PoolOrigen_Pool FOREIGN KEY (PoolID)
            REFERENCES planificacion.Pool (PoolID)
    );
    PRINT 'planificacion.PoolOrigen creada.';
END
ELSE PRINT 'planificacion.PoolOrigen ya existía.';
GO

/* -------------------------------------------------- catálogo de códigos RRHH */
IF OBJECT_ID('planificacion.CodigoPayroll', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.CodigoPayroll (
        Codigo      NVARCHAR(30)  NOT NULL
            CONSTRAINT PK_Plan_CodigoPayroll PRIMARY KEY,

        -- piso         = está en la operación atendiendo (cuenta como dotación)
        -- ausente      = tenía horas programadas y no vino (ausentismo)
        -- capacitacion = vino, cobra, pero no está en la cola
        -- licencia     = ni programado ni esperado (vacaciones, licencias largas)
        -- otro         = no clasificado todavía; se cuenta aparte y se avisa
        Clase       NVARCHAR(20)  NOT NULL,
        Descripcion NVARCHAR(200) NULL,

        CONSTRAINT CK_Plan_CodigoPayroll_Clase
            CHECK (Clase IN ('piso', 'ausente', 'capacitacion', 'licencia', 'otro'))
    );
    PRINT 'planificacion.CodigoPayroll creada.';
END
ELSE PRINT 'planificacion.CodigoPayroll ya existía.';
GO

/* ------------------------------------------------- columnas nuevas: Campana */
IF COL_LENGTH('planificacion.Campana', 'PacienciaHorizonteSeg') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        -- A qué horizonte se ajusta la exponencial de Erlang A. La paciencia real
        -- no es exponencial; lo que se busca es la exponencial que reproduce el
        -- abandono observado EN EL RANGO DONDE SE ESPERA de verdad, no la que
        -- mejor ajusta una cola de 10 minutos que casi nadie recorre.
        PacienciaHorizonteSeg SMALLINT NULL,
        -- 'km' (Kaplan-Meier ajustado al horizonte) | 'mle' | 'manual'
        PacienciaOrigen       NVARCHAR(20) NULL,
        -- Desglose informativo del shrinkage, para que el número se pueda explicar.
        ShrinkageAusentismo   DECIMAL(4,3) NULL,
        ShrinkageCapacitacion DECIMAL(4,3) NULL,
        ShrinkageOrigen       NVARCHAR(20) NULL,   -- 'payroll' | 'manual'
        ShrinkageMedidoEn     DATETIME2(0) NULL;
    PRINT 'Columnas de calibración agregadas a planificacion.Campana.';
END
ELSE PRINT 'planificacion.Campana ya tenía las columnas de calibración.';
GO

/* ------------------------------------------- columnas nuevas: Requerimiento */
IF COL_LENGTH('planificacion.Requerimiento', 'OperadoresPlanificados') IS NULL
BEGIN
    ALTER TABLE planificacion.Requerimiento ADD
        -- Lo que RRHH tiene efectivamente citado para ese intervalo (de payroll o
        -- payroll_futuro). NULL = no hay malla cargada todavía para ese día.
        OperadoresPlanificados SMALLINT NULL,
        -- La diferencia es el número que se lleva a la reunión: negativo = falta
        -- gente para cumplir el objetivo, positivo = sobra.
        Brecha                 SMALLINT NULL;
    PRINT 'Columnas de comparación agregadas a planificacion.Requerimiento.';
END
ELSE PRINT 'planificacion.Requerimiento ya tenía las columnas de comparación.';
GO

/* ====================================================================
   SIEMBRA
   ==================================================================== */

/* Códigos de payroll observados en jun-ago 2026. Los que no estén acá caen en
   'otro' y la calibración los reporta aparte para que alguien los clasifique en
   vez de que se pierdan en el promedio. */
IF NOT EXISTS (SELECT 1 FROM planificacion.CodigoPayroll)
BEGIN
    INSERT INTO planificacion.CodigoPayroll (Codigo, Clase, Descripcion) VALUES
        ('P',              'piso',         'Presente en la operación'),
        ('PRES',           'piso',         'Presencial'),
        ('BU LOGUEO',      'piso',         'Refuerzo logueado'),
        ('BU FDS',         'piso',         'Refuerzo de fin de semana'),
        ('ABS',            'ausente',      'Ausente: tenía horas programadas y no vino'),
        ('AEJ',            'ausente',      'Ausencia justificada'),
        ('AENJ',           'ausente',      'Ausencia no justificada'),
        ('AERH',           'ausente',      'Ausencia autorizada por RRHH'),
        ('DES',            'ausente',      'Descanso otorgado'),
        ('MED',            'ausente',      'Licencia médica del día'),
        ('ART',            'ausente',      'ART'),
        ('ART Ext',        'ausente',      'ART extendida'),
        ('SUSP',           'ausente',      'Suspensión'),
        ('FFJ',            'ausente',      'Falta con aviso justificada'),
        ('MAT',            'ausente',      'Licencia por maternidad'),
        ('MUS',            'ausente',      'Licencia por mudanza'),
        ('PAT',            'ausente',      'Licencia por paternidad'),
        ('DEP',            'ausente',      'Licencia deportiva'),
        ('EF No Rem',      'ausente',      'Enfermedad no remunerada'),
        ('HEI',            'ausente',      'Hora de entrada irregular'),
        ('CAPA',           'capacitacion', 'En capacitación: cobra pero no atiende'),
        ('CAPAEX',         'capacitacion', 'Capacitación externa'),
        ('DICTADO-CAPA',   'capacitacion', 'Dictando capacitación'),
        ('DICTADO-POST',   'capacitacion', 'Dictando post-capacitación'),
        ('DICTADO-TALLER', 'capacitacion', 'Dictando taller'),
        ('VA-24',          'licencia',     'Vacaciones 2024'),
        ('VA-25',          'licencia',     'Vacaciones 2025'),
        ('VA-26',          'licencia',     'Vacaciones 2026'),
        ('LP',             'licencia',     'Licencia sin goce'),
        ('LM',             'licencia',     'Licencia médica larga'),
        ('LMR',            'licencia',     'Licencia médica remunerada'),
        ('LMNR',           'licencia',     'Licencia médica no remunerada'),
        ('RP',             'licencia',     'Reserva de puesto'),
        ('EM',             'licencia',     'Excedencia por maternidad'),
        ('FNT',            'licencia',     'Franco no trabajado'),
        ('BJT',            'licencia',     'Baja transitoria'),
        ('IT',             'licencia',     'Incapacidad temporal'),
        ('GREMIO',         'licencia',     'Licencia gremial');
    PRINT 'Catálogo de códigos de payroll sembrado.';
END
GO

/* Sub-campañas de RRHH cuya gente atendió el teléfono de Voltara en agosto 2026.
   Salió de cruzar los agentes de TMO_Voltara contra nómina y operadores: NO es la
   lista de las que "parecen" telefónicas. Se ajusta desde la pantalla. */
IF NOT EXISTS (SELECT 1 FROM planificacion.PoolOrigen)
BEGIN
    DECLARE @general INT = (SELECT PoolID FROM planificacion.Pool
                            WHERE CampanaID = 20 AND Nombre = 'General');
    DECLARE @chico   INT = (SELECT PoolID FROM planificacion.Pool
                            WHERE CampanaID = 20 AND Nombre = 'Reclamos y Grandes Cuentas');
    DECLARE @consumo INT = (SELECT PoolID FROM planificacion.Pool
                            WHERE CampanaID = 20 AND Nombre = 'Comercial Consumo');

    IF @general IS NOT NULL
    INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota) VALUES
        (@general, 100, 'T1 - Teléfono (58 agentes atendiendo en agosto 2026)'),
        (@general, 106, 'T1 - Emergencias (10)'),
        (@general,  53, 'Digital (13) — también atienden teléfono'),
        (@general,  58, 'Backoffice RRSS (8) — también atienden teléfono'),
        (@general,  89, 'BackOffice Electro (7)'),
        (@general, 139, 'Agrupadas - Digital (7)'),
        (@general,  64, 'Artefactos Dañados (6)'),
        (@general,  54, 'T2T3 - Teléfono (5)'),
        (@general, 178, 'Contingencia (4)'),
        (@general, 132, 'T2T3 - Digital (4)'),
        (@general, 131, 'BackOffice (3)'),
        (@general, 165, 'Ajustes - Lecturas (1)'),
        (@general, 146, 'Gestión SVP (1)');

    IF @consumo IS NOT NULL
    INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota) VALUES
        (@consumo, 177, 'T1 - Consumo (4 agentes) — el grupo dedicado de COMERCIAL-CONSUMO');

    PRINT 'Origen de gente de los pools de Voltara sembrado.';
END
GO

/* Paciencia y shrinkage medidos (jun-ago 2026). Reemplazan a los de arranque:
   la paciencia pasa de 853s (MLE global) a 1.126s (Kaplan-Meier ajustado a los
   primeros 60 segundos) y el shrinkage de 0,300 supuesto a 0,272 medido. */
UPDATE planificacion.Campana
SET PacienciaSeg           = 1126,
    PacienciaHorizonteSeg  = 60,
    PacienciaOrigen        = 'km',
    ShrinkageDefault       = 0.272,
    ShrinkageAusentismo    = 0.171,
    ShrinkageCapacitacion  = 0.093,
    ShrinkageOrigen        = 'payroll',
    ShrinkageMedidoEn      = SYSUTCDATETIME(),
    Nota                   = 'Paciencia por Kaplan-Meier sobre jun-ago 2026 ajustada a 60s (a los 60s sigue esperando el 94,8%). Shrinkage medido sobre dbo.payroll: 17,1% ausentismo + 9,3% capacitación.'
WHERE CampanaID = 20 AND (PacienciaOrigen IS NULL OR PacienciaOrigen <> 'manual');
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT p.Nombre AS Pool, o.CampanaRRHHID, c.sub_campana, o.Nota
FROM planificacion.PoolOrigen o
JOIN planificacion.Pool p ON p.PoolID = o.PoolID
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID
ORDER BY p.Nombre, o.CampanaRRHHID;

SELECT Clase, COUNT(*) AS codigos FROM planificacion.CodigoPayroll GROUP BY Clase;

SELECT CampanaID, PacienciaSeg, PacienciaHorizonteSeg, PacienciaOrigen,
       ShrinkageDefault, ShrinkageAusentismo, ShrinkageCapacitacion, ShrinkageOrigen
FROM planificacion.Campana;
GO
