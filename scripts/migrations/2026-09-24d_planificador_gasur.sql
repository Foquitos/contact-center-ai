/* ============================================================================
   Datos — Planificador: alta de Gasur (Uruguay, distribución de garrafas)
   Fecha: 2026-09-24 (posterior a 2026-09-24c_planificador_cortes_enre_tipo.sql)
   Autor: equipo Acme

   QUÉ HACE
   --------
   1. Da de alta a Gasur en calidad (no existía): permiso `template:gasur`
      (nace inactivo y SIN asignar a ningún rol), empresa "Gasur" y campaña
      "Gasur" con CampanaID 30 FIJO, porque el código del planificador la
      busca por ese id (CAMPANA_GASUR en planificador_datos.py). Si el 30 ya es
      otra campaña, no se toca nada.
   2. La da de alta en el planificador:

     Campana          CampanaID 30, con paciencia, shrinkage y disponibilidad medidos
     Pool             'Gasur', un solo pool
     Skill            1 = sintético: las 12 colas de entrada (C100C, C103C...) las
                      atiende la misma gente, así que son un solo skill
     PoolOrigen       sub-campaña de RRHH 121 (Gasur)
     PoolRefuerzo     sub-campaña 135 (Despacho), ver abajo
     Disponibilidad   1 en todo el día (medida sobre 365 días)
     Pronóstico       clima + GBDT del nivel + persistencia, 26 semanas de base

   El código que lee sus llamadas (FuenteGasur en planificador_datos.py) tiene
   que estar deployado ANTES o junto: sin él la campaña aparece en el selector
   como "sin fuente de datos" y los crons la saltean, sin romper nada.

   Gasur aparece también en Calidad como empresa con una campaña y sin
   plantillas. Para verla en el planificador, el rol necesita planificador.view
   y template:gasur (o templates:manage): asignarlo es decisión del responsable.

   LO MEDIDO (2026-09-24, sin escribir nada: calibración y backtest con la
   configuración armada en memoria)
   -----------------------------------------------------------------------------
   - Fuente: dbo.[Gasur Llamadas], una fila por llamada, intradía. Hora por
     hora coincide exacto con dbo.[Gasur Eficiencia]. Conectados:
     dbo.[Gasur Actividad] (día cerrado); la semana del 14/09 da 527,3 h contra
     527,5 h del tablero.
   - Volumen: ~500-1.500 entrantes por día, TMO ~80 s (sólo conversación: el
     reporte no trae ACW). Estacionalidad de calefacción: ene-2026 7.500
     entrantes, jun-2026 45.000.
   - Paciencia: Kaplan-Meier sobre 365 días (270.149 llamadas, 25.151
     abandonos) ajustada a 60 s = 208 s. A los 30 s sigue esperando el 90% y a
     los 60 s el 75%: mucho menos paciente que Hidra (2.482 s).
   - Shrinkage de nómina (dbo.payroll, 365 días, sub-campaña 121): 0,8% = 0,6%
     ausentismo + 0,3% capacitación. Casi no hay ausencias cargadas en los
     puestos de operador; cierra contra los conectados (la semana del 14/09,
     519,8 h de turno y 527,5 h logueadas).
   - Disponibilidad: 1 en todas las horas medidas (1.448 intervalos donde la
     cola ató). Con 3 a 8 operadores Erlang es pesimista (43% de intervalos sin
     explicar), como pasa en Voltara de noche.
   - Despacho (135) en la línea: medido mes a mes de sep-2025 a sep-2026, 0 a
     2,7% de las horas conectadas, 4,3% en junio y 4,8% en julio de 2026 (el
     pico de invierno) y 0 desde agosto. Es gente que pasa al teléfono cuando no
     se llega: refuerzo, como Digital en Voltara.
   - Pronóstico: backtest sobre un año corrido (2025-09-22 a 2026-09-20) contra
     el dbo.Forecast de Gasur que ya usa la operación, error del total diario
     (MAPE) y WAPE por media hora:
         configuración base (la de Voltara sin extras)
           plan de la mañana (antelación 0)   46,2% / 58,0%   cliente 47,7% / 55,0%
           a 7 días                           48,7% / 58,7%
         con lo que se prende acá
           plan de la mañana (antelación 0)   33,0% / 51,7%   (hábil 33,6%, sábado 31,2%,
                                                              domingo 32,1%; cliente 40,8 /
                                                              57,3 / 71,5%)
           plan de mañana    (antelación 1)   36,9% / 53,1%
           a 7 días                           45,2% / 56,3%   (en días hábiles a 7 días el
                                                              del cliente gana: 40,8% vs 44,4%)
     Es una serie MUY ruidosa: 270 a 1.800 llamadas por día en la misma semana,
     y con ~25 llamadas por media hora el WAPE por media hora no baja de ~40%
     aunque se acierte el total del día. Lo que se prende:
       · Clima (Montevideo) + GBDT del nivel diario (peso 0,4): es gas para
         calefacción, el desvío del día correlaciona -0,5 con la temperatura.
         Solo el clima: 46,9% -> 42,3%; con el GBDT, 39,3% (antelación 1).
       · Persistencia 0,7 hoy / 0,5 los 6 días siguientes: el desvío de un día
         correlaciona 0,70 con el del siguiente (en Hidra 0,53).
         Plan de la mañana 41,3% (clima) -> 31,9% (clima + GBDT + persistencia).
         Probado 0,6/0,4 (32,0%) y 0,85/0,6 (32,1%): da lo mismo.
       · SemanasBase 26 (Voltara y Hidra 52): desde el 26/03/2026 la línea atiende
         hasta las 24 (antes cerraba a las 21) y con 52 semanas la mediana de
         esas horas todavía da 0. En el año pierde un poco (31,9% -> 33,0%) pero
         desde agosto el WAPE por media hora baja 45,0% -> 42,1% con el mismo
         error diario. 13 semanas: igual de diario y sesgo +5,6%.
     Probado y descartado: reescalado intradía (empeora a cualquier hora: desde
     las 12, 50,8% -> 56,1% del resto del día) y feriado como sábado (sólo hay
     5 feriados no laborables por año: no hay con qué medirlo; quedan como
     domingo). Los feriados LABORABLES de Uruguay (Carnaval, Turismo...) no se
     tratan como feriado: medidos van de x0,2 a x2,6 sin patrón.

   A CONFIRMAR POR LA OPERACIÓN
   -----------------------------
   - Objetivo de nivel de servicio: 80% en 10 s. Los 10 s son el umbral del
     "Nivel de Servicio %" de dbo.[Gasur Eficiencia] y del Tablero_Gasur; el
     80% no se conoce del contrato. Se cambia desde Configuración.

   DESPUÉS DE APLICARLA
   --------------------
   1. IMPRESCINDIBLE: bajar el histórico del clima de Montevideo (una vez).
      Gasur nace con el clima y el GBDT prendidos, y sin el histórico el
      modelo del clima no entrena (pide 300 días):
        python scripts/clima_voltara.py --campana 30 --desde 2022-01-01
      Después clima_diario.sh (--todas) lo mantiene al día.
   2. Recalcular el plan de Gasur desde la pantalla (o esperar al cron de las
      06:00/14:00, que recorre todas las campañas con fuente).
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------------------------------------------------------------------------
   Si el CampanaID 30 ya es otra campaña, no se siembra nada (NOEXEC saltea el
   resto de los batches; RAISERROR solo cortaría éste).
   --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = 30 AND Nombre <> N'Gasur')
BEGIN
    RAISERROR('calidad.Campanas 30 ya es otra campaña: revisar CAMPANA_GASUR antes de sembrar.', 16, 1);
    SET NOEXEC ON;
END
GO

/* ---------------------------------------------------------------------------
   Permiso, empresa y campaña de calidad
   --------------------------------------------------------------------------- */
BEGIN TRANSACTION;

DECLARE @PermisoID int = (SELECT id FROM pagina_web.Permissions WHERE code = 'template:gasur');
IF @PermisoID IS NULL
BEGIN
    INSERT INTO pagina_web.Permissions (code, description, activo)
    VALUES ('template:gasur', 'Acceso a Gasur (planificador, auditorías y plantillas)', 0);
    SET @PermisoID = SCOPE_IDENTITY();
END

DECLARE @EmpresaID int = (SELECT EmpresaID FROM calidad.Empresas WHERE Nombre = 'Gasur');
IF @EmpresaID IS NULL
BEGIN
    INSERT INTO calidad.Empresas (Nombre, RequiredPermissionID) VALUES ('Gasur', @PermisoID);
    SET @EmpresaID = SCOPE_IDENTITY();
END

-- Con id fijo y no por sp_CrearCampana (que toma el siguiente IDENTITY): el
-- código la necesita de antemano. Insertar un id mayor al actual adelanta el
-- IDENTITY, así que la próxima campaña que se cree sigue en 31.
IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = 30)
BEGIN
    SET IDENTITY_INSERT calidad.Campanas ON;
    INSERT INTO calidad.Campanas (CampanaID, Nombre, EmpresaID, PlataformaID)
    VALUES (30, N'Gasur', @EmpresaID,
            (SELECT PlataformaID FROM calidad.Plataformas WHERE nombre = 'Otro'));
    SET IDENTITY_INSERT calidad.Campanas OFF;
    PRINT 'Campaña Gasur (30) creada en calidad.';
END
ELSE PRINT 'La campaña 30 (Gasur) ya existía en calidad.';

COMMIT TRANSACTION;
GO

-- Activa template:gasur ahora que la empresa tiene una campaña activa (lo mismo
-- que hace sp_CrearCampana al final).
EXEC calidad.sp_SincronizarPermisosEmpresas;
GO

/* ---------------------------------------------------------------------------
   Planificador
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM planificacion.Campana WHERE CampanaID = 30)
BEGIN
    INSERT INTO planificacion.Campana
        (CampanaID, Activa, IntervaloMin, MaxOcupacion, ShrinkageDefault, PacienciaSeg, Nota,
         PacienciaHorizonteSeg, PacienciaOrigen,
         ShrinkageAusentismo, ShrinkageCapacitacion, ShrinkageOrigen, ShrinkageMedidoEn,
         SemanasBase, DiasNivelReciente, NivelPorTipoDeDia,
         ClimaLat, ClimaLon, ClimaActivo,
         NivelGbdt, NivelGbdtPeso,
         FeriadoComoSabado, PuenteFactor, PersistenciaPesoHoy, PersistenciaPesoResto,
         PersistenciaDias, IntradiaDesdeHora)
    VALUES
        (30, 1, 30, 0.850, 0.008, 208,
         N'Gasur (Uruguay). Paciencia por Kaplan-Meier sobre 365 días ajustada a 60 s (a los 60 s sigue esperando el 75%). Shrinkage medido sobre dbo.payroll, 365 días: 0,6% ausentismo + 0,3% capacitación. Feriados de Uruguay (sólo los no laborables).',
         60, N'medido',
         0.006, 0.003, N'medido', SYSUTCDATETIME(),
         26, 28, 1,
         -34.900000, -56.190000, 1,
         1, 0.400,
         0, NULL, 0.700, 0.500, 7, NULL);
    PRINT 'Campaña Gasur sembrada en el planificador.';
END
ELSE PRINT 'Gasur ya estaba dada de alta en el planificador: no se toca.';
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Pool WHERE CampanaID = 30)
BEGIN
    INSERT INTO planificacion.Pool (CampanaID, Nombre, MinOperadores, Nota) VALUES
        (30, N'Gasur', 0,
         N'Una sola cola (las 12 colas de entrada las atiende la misma gente). ~17 operadores de la sub-campaña 121.');
    PRINT 'Pool de Gasur sembrado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Skill WHERE CampanaID = 30)
BEGIN
    DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 30 AND Nombre = N'Gasur');

    INSERT INTO planificacion.Skill
        (CampanaID, SkillID, Nombre, PoolID, ObjetivoNds, UmbralSeg, MaxAbandono, PacienciaSeg, Nota)
    VALUES
        (30, 1, 'Gasur', @pool, 0.800, 10, NULL, 208,
         N'Skill sintético (todas las colas entrantes). Umbral de 10 s = el del Nivel de Servicio de Gasur Eficiencia; el 80% a confirmar contra el contrato.');
    PRINT 'Skill de Gasur sembrado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.PoolOrigen o
               JOIN planificacion.Pool p ON p.PoolID = o.PoolID
               WHERE p.CampanaID = 30)
BEGIN
    DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 30 AND Nombre = N'Gasur');

    INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota) VALUES
        (@pool, 121, N'Gasur. Semana del 14/09/2026: 527,5 de 527,5 h logueadas en la línea.');
    PRINT 'Origen de la gente de Gasur sembrado.';
END
GO

IF OBJECT_ID('planificacion.PoolRefuerzo', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM planificacion.PoolRefuerzo r
                   JOIN planificacion.Pool p ON p.PoolID = r.PoolID
                   WHERE p.CampanaID = 30)
BEGIN
    DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 30 AND Nombre = N'Gasur');

    INSERT INTO planificacion.PoolRefuerzo (PoolID, CampanaRRHHID, Nota) VALUES
        (@pool, 135, N'Despacho. Pasa al teléfono en el pico de invierno (4-5% de las horas en jun-jul 2026).');
    PRINT 'Despacho sembrado como refuerzo de Gasur.';
END
GO

/* Medida el 2026-09-24 sobre 365 días: 1 en todas las horas con muestras (8 a 22).
   Fuera del horario de atención (00 a 08) no hay intervalos: 1 a mano. */
IF NOT EXISTS (SELECT 1 FROM planificacion.Disponibilidad WHERE CampanaID = 30)
BEGIN
    INSERT INTO planificacion.Disponibilidad
        (CampanaID, PoolID, DiaSemana, HoraDesde, HoraHasta, Factor, Origen, Muestras)
    VALUES
        (30, NULL, 0, 0,  8, 1.000, 'manual', NULL),
        (30, NULL, 0, 8, 24, 1.000, 'medido', 1448);
    PRINT 'Disponibilidad de Gasur sembrada.';
END
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT c.CampanaID, cc.Nombre, e.Nombre AS Empresa, pe.code, pe.activo,
       c.Activa, c.ShrinkageDefault, c.PacienciaSeg, c.ClimaActivo, c.NivelGbdt,
       c.SemanasBase, c.PersistenciaPesoHoy,
       p.Nombre AS Pool, s.SkillID, s.Nombre AS Skill, s.ObjetivoNds, s.UmbralSeg,
       o.CampanaRRHHID AS Origen, r.CampanaRRHHID AS Refuerzo
FROM planificacion.Campana c
JOIN calidad.Campanas cc ON cc.CampanaID = c.CampanaID
JOIN calidad.Empresas e ON e.EmpresaID = cc.EmpresaID
LEFT JOIN pagina_web.Permissions pe ON pe.id = e.RequiredPermissionID
JOIN planificacion.Pool p ON p.CampanaID = c.CampanaID
JOIN planificacion.Skill s ON s.CampanaID = c.CampanaID AND s.PoolID = p.PoolID
LEFT JOIN planificacion.PoolOrigen o ON o.PoolID = p.PoolID
LEFT JOIN planificacion.PoolRefuerzo r ON r.PoolID = p.PoolID
WHERE c.CampanaID = 30;
GO

SET NOEXEC OFF;
GO
