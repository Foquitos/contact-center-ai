/* ============================================================================
   Feature — Planificador: pronóstico de llamadas y dimensionamiento de operadores
   Fecha: 2026-09-03
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   El schema `planificacion` completo:

     Configuración   Campana, Pool, Skill, Asignacion, Disponibilidad
     Insumos         Evento (calendario de días atípicos), Ajuste (overrides)
     Resultados      Corrida, Pronostico, Requerimiento
     Permisos        planificador.view, planificador.edit

   POR QUÉ
   -------
   Hasta hoy el pronóstico de Voltara vive en `dbo.Forecast`, que se carga A MANO
   y por eso se desactualiza: en la última semana de agosto de 2026 sobreestimaba
   entre 28% y 73% todos los días, simplemente porque el número había quedado
   congelado cuando el volumen bajó. Y de ese pronóstico a "cuánta gente pongo"
   no hay nada: lo resuelve cada quien en su Excel.

   LAS TRES DECISIONES QUE EXPLICAN EL DISEÑO
   -------------------------------------------
   1. **Pool, no skill.** Los operadores de Voltara son multiskill: la suma de
      "Agentes Logueados" de los 9 skills da ~355 en un intervalo, pero en todo
      el día hay 119 agentes distintos (cada uno en 3 a 5 colas). Dimensionar con
      un Erlang C por skill por separado pide el triple de gente de la necesaria,
      porque tira a la basura la economía de escala de la cola compartida. Por eso
      el requerimiento se calcula por POOL y los skills se agrupan en pools.

      Medido sobre agosto de 2026, Voltara tiene tres pools y no uno:
        - pool grande (~170 personas): Emergencias, Comercial, TOC,
          Electrodependientes, Emergencias-Empresarial, CNR-Empresarial. Es el 98%
          del volumen.
        - grupo chico (8 personas): Reclamo-Daño y Grandes-Cuentas.
        - dedicados (6 personas): Comercial-Consumo, que es el único skill donde
          los agentes no tocan ninguna otra cola (100% de dedicación).

   2. **El share del cliente es un parámetro, no un dato del pasado.** Voltara
      reparte sus llamadas entre varios contact centers (Acme es BPO-04, Startrek
      es BPO-05) y nuestra porción va a pasar de ~40% a hasta 70%. Todo lo que
      tenemos históricamente es `demanda_de_Voltara x nuestro_share` mezclado en
      una sola serie. `Asignacion` separa las dos cosas para que el pronóstico
      pueda ser de la demanda total y el share se aplique aparte, con vigencia por
      fecha. Es lo que habilita el what-if "si me dan el 70%, cuánta gente hace
      falta".

   3. **La disponibilidad se mide, no se supone.** Erlang asume que los N
      operadores están dedicados a la cola todo el intervalo, y eso nunca pasa
      (pausas, ACW largo, otra cola). Contrastando el motor contra el 12 de agosto
      de 2026, la dotación efectiva que explica el NDS real es 0,82 de la dotación
      presente en el turno diurno, con una dispersión chica (0,63 a 0,87). O sea
      que el modelo reproduce la realidad si —y solo si— se le aplica ese factor.
      `Disponibilidad` lo guarda por franja y día, y se recalcula de los datos.

   SOBRE EL NIVEL DE SERVICIO
   --------------------------
   El objetivo contractual de Voltara es 80% en 20 segundos, y Electrodependientes
   además exige 100% de nivel de atención (cero abandonos). OJO: el reporte que se
   mira hoy calcula el NDS con umbral de 30 segundos, no 20 (`Contestadas Umbral`
   coincide exactamente con la suma acumulada de los buckets hasta 30s en los 8
   skills). Medido a 20s en agosto de 2026: Emergencias 86,3% (cumple), Comercial
   69,8%, Reclamo-Daño 45,6% y Grandes-Cuentas 36,9% (no cumplen). Por eso el
   umbral es una columna por skill y no una constante: la pantalla tiene que poder
   mostrar el número contractual y no el del reporte.

   Además el NDS se mide sobre las ATENDIDAS, así que las que abandonan no restan.
   `Requerimiento` guarda las dos lecturas (NdsAtendidas y NdsEntrantes) para que
   la diferencia esté a la vista.

   CÓMO CORRER
   -----------
   Contra la BD Acme, con un usuario con DDL. Idempotente y aditiva: crea el
   schema y las tablas si faltan, siembra la configuración de Voltara y no toca
   nada de lo existente. Puede correrse ANTES del deploy.

   NO TOCA `dbo.Forecast`. Esa tabla la sigue usando lo que ya la lee; el
   planificador escribe en `planificacion.Pronostico` y recién cuando esté
   validado se decide si además la replica.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF SCHEMA_ID('planificacion') IS NULL
BEGIN
    EXEC('CREATE SCHEMA planificacion');
    PRINT 'Schema planificacion creado.';
END
GO

/* ====================================================================
   CONFIGURACIÓN
   ==================================================================== */

/* ------------------------------------------------------------- campaña */
IF OBJECT_ID('planificacion.Campana', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Campana (
        CampanaID          INT           NOT NULL
            CONSTRAINT PK_Plan_Campana PRIMARY KEY,

        Activa             BIT           NOT NULL
            CONSTRAINT DF_Plan_Campana_Activa DEFAULT (1),

        -- Duración del intervalo de planificación, en minutos. Los reportes de
        -- Voltara, Hidra y Gasur vienen de a 30.
        IntervaloMin       SMALLINT      NOT NULL
            CONSTRAINT DF_Plan_Campana_Intervalo DEFAULT (30),

        -- Techo de ocupación. Una dotación que cumple el NDS con la gente al 95%
        -- de ocupación no se sostiene: sube el ausentismo y sube el TMO, que es
        -- justo el dato que el modelo tomó como fijo. NULL = sin techo.
        MaxOcupacion       DECIMAL(4,3)  NULL
            CONSTRAINT DF_Plan_Campana_Ocupacion DEFAULT (0.850),

        -- Shrinkage DE NÓMINA: ausentismo, vacaciones, licencias y capacitación,
        -- o sea la gente que está en la lista pero no viene ese día.
        -- OJO, NO incluye las pausas ni el ACW ni el tiempo en otra cola: eso ya
        -- está descontado en `Disponibilidad`, que se mide dentro del intervalo.
        -- Meterlo en los dos lados descuenta dos veces lo mismo y sobredimensiona.
        -- El 0,300 es un valor de arranque a calibrar con los datos de RRHH.
        ShrinkageDefault   DECIMAL(4,3)  NOT NULL
            CONSTRAINT DF_Plan_Campana_Shrinkage DEFAULT (0.300),

        -- Paciencia media del cliente en segundos (Erlang A). Se estima de los
        -- datos con el MLE exponencial censurado: abandonos / suma de esperas.
        PacienciaSeg       INT           NULL,

        Nota               NVARCHAR(400) NULL,
        ActualizadoPor     INT           NULL,
        ActualizadoEn      DATETIME2(0)  NOT NULL
            CONSTRAINT DF_Plan_Campana_Fecha DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT CK_Plan_Campana_Intervalo  CHECK (IntervaloMin IN (15, 30, 60)),
        CONSTRAINT CK_Plan_Campana_Ocupacion  CHECK (MaxOcupacion IS NULL OR (MaxOcupacion > 0 AND MaxOcupacion <= 1)),
        CONSTRAINT CK_Plan_Campana_Shrinkage  CHECK (ShrinkageDefault >= 0 AND ShrinkageDefault < 0.95),
        CONSTRAINT CK_Plan_Campana_Paciencia  CHECK (PacienciaSeg IS NULL OR PacienciaSeg > 0),
        CONSTRAINT FK_Plan_Campana_Campana FOREIGN KEY (CampanaID)
            REFERENCES calidad.Campanas (CampanaID)
    );
    PRINT 'planificacion.Campana creada.';
END
ELSE PRINT 'planificacion.Campana ya existía.';
GO

/* ---------------------------------------------------------------- pool */
IF OBJECT_ID('planificacion.Pool', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Pool (
        PoolID          INT           IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_Pool PRIMARY KEY,
        CampanaID       INT           NOT NULL,
        Nombre          NVARCHAR(80)  NOT NULL,

        -- Cobertura mínima: cuántos tienen que estar sí o sí aunque el pronóstico
        -- dé cero. Sin esto la madrugada queda sin nadie.
        MinOperadores   SMALLINT      NOT NULL
            CONSTRAINT DF_Plan_Pool_Min DEFAULT (0),

        Activo          BIT           NOT NULL
            CONSTRAINT DF_Plan_Pool_Activo DEFAULT (1),
        Nota            NVARCHAR(400) NULL,

        CONSTRAINT CK_Plan_Pool_Min CHECK (MinOperadores >= 0),
        CONSTRAINT FK_Plan_Pool_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID),
        CONSTRAINT UQ_Plan_Pool UNIQUE (CampanaID, Nombre)
    );
    PRINT 'planificacion.Pool creada.';
END
ELSE PRINT 'planificacion.Pool ya existía.';
GO

/* --------------------------------------------------------------- skill */
/* SkillID es el id del normalizador de LA CAMPAÑA (para Voltara,
   dbo.[Voltara normalizador por Skill]), no calidad.Skills: ese catálogo no tiene
   cargados los skills de Voltara (CampanaID 20 no devuelve filas). El nombre se
   guarda desnormalizado a propósito, para que la pantalla no dependa de una
   tabla distinta por campaña. */
IF OBJECT_ID('planificacion.Skill', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Skill (
        CampanaID     INT           NOT NULL,
        SkillID       INT           NOT NULL,
        Nombre        NVARCHAR(80)  NOT NULL,
        PoolID        INT           NULL,          -- NULL = no se dimensiona

        -- Objetivo contractual. Voltara: 80% en 20s. Se guarda por skill porque
        -- difiere (y porque el reporte que se mira hoy usa 30s, no 20).
        ObjetivoNds   DECIMAL(4,3)  NULL,
        UmbralSeg     SMALLINT      NULL,

        -- Techo de abandono. Es lo que rige en Electrodependientes, donde el
        -- compromiso es 100% de nivel de atención y no un tiempo de espera.
        MaxAbandono   DECIMAL(5,4)  NULL,

        -- Paciencia propia del skill; si es NULL se usa la de la campaña.
        PacienciaSeg  INT           NULL,

        Activo        BIT           NOT NULL
            CONSTRAINT DF_Plan_Skill_Activo DEFAULT (1),
        Nota          NVARCHAR(400) NULL,

        CONSTRAINT PK_Plan_Skill PRIMARY KEY (CampanaID, SkillID),
        CONSTRAINT CK_Plan_Skill_Nds       CHECK (ObjetivoNds IS NULL OR (ObjetivoNds > 0 AND ObjetivoNds <= 1)),
        CONSTRAINT CK_Plan_Skill_Umbral    CHECK (UmbralSeg IS NULL OR UmbralSeg > 0),
        CONSTRAINT CK_Plan_Skill_Abandono  CHECK (MaxAbandono IS NULL OR (MaxAbandono >= 0 AND MaxAbandono <= 1)),
        CONSTRAINT CK_Plan_Skill_Paciencia CHECK (PacienciaSeg IS NULL OR PacienciaSeg > 0),
        CONSTRAINT FK_Plan_Skill_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID),
        CONSTRAINT FK_Plan_Skill_Pool FOREIGN KEY (PoolID)
            REFERENCES planificacion.Pool (PoolID)
    );
    PRINT 'planificacion.Skill creada.';
END
ELSE PRINT 'planificacion.Skill ya existía.';
GO

/* ---------------------------------------------------------- asignación */
/* Qué porcentaje de la demanda del cliente nos toca a nosotros, con vigencia.
   Es la tabla que separa "cuántas llamadas tiene Voltara" de "cuántas nos manda",
   que hoy están mezcladas en una sola serie histórica y por eso el pronóstico se
   rompe cuando el reparto cambia. SkillID NULL = vale para toda la campaña. */
IF OBJECT_ID('planificacion.Asignacion', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Asignacion (
        AsignacionID   INT           IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_Asignacion PRIMARY KEY,
        CampanaID      INT           NOT NULL,
        SkillID        INT           NULL,
        VigenteDesde   DATE          NOT NULL,
        VigenteHasta   DATE          NULL,          -- NULL = sigue vigente
        Porcentaje     DECIMAL(5,4)  NOT NULL,      -- 0.40 = nos llega el 40%
        Nota           NVARCHAR(400) NULL,
        CreadoPor      INT           NULL,
        CreadoEn       DATETIME2(0)  NOT NULL
            CONSTRAINT DF_Plan_Asignacion_Fecha DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT CK_Plan_Asignacion_Pct   CHECK (Porcentaje > 0 AND Porcentaje <= 1),
        CONSTRAINT CK_Plan_Asignacion_Rango CHECK (VigenteHasta IS NULL OR VigenteHasta >= VigenteDesde),
        CONSTRAINT FK_Plan_Asignacion_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID)
    );
    CREATE INDEX IX_Plan_Asignacion_vigencia
        ON planificacion.Asignacion (CampanaID, VigenteDesde) INCLUDE (SkillID, VigenteHasta, Porcentaje);
    PRINT 'planificacion.Asignacion creada.';
END
ELSE PRINT 'planificacion.Asignacion ya existía.';
GO

/* ------------------------------------------------------- disponibilidad */
/* Qué proporción de los operadores presentes está realmente tomando llamadas de
   la cola. Se recalcula de los datos: se busca, para cada intervalo con cola
   real, la dotación efectiva que explica el NDS observado, y se la divide por la
   dotación presente. Medido para Voltara el 12/08/2026 da 0,82 de día. De noche
   el indicador satura (el NDS real llega a 100% y cualquier dotación lo cumple),
   así que solo se estima con intervalos donde la cola efectivamente ató. */
IF OBJECT_ID('planificacion.Disponibilidad', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Disponibilidad (
        -- Clave sustituta y no la natural: PoolID es NULLable (NULL = toda la
        -- campaña) y SQL Server no admite columnas nulables en una PRIMARY KEY.
        -- La unicidad de la clave natural la garantiza el índice UNIQUE de abajo,
        -- que además trata a los NULL como iguales entre sí: exactamente lo que
        -- se quiere acá (una sola franja "toda la campaña" por día y hora).
        DisponibilidadID INT       IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_Disp PRIMARY KEY,

        CampanaID    INT           NOT NULL,
        PoolID       INT           NULL,           -- NULL = toda la campaña
        DiaSemana    TINYINT       NOT NULL,       -- 1 = lunes ... 7 = domingo; 0 = todos
        HoraDesde    TINYINT       NOT NULL,       -- 0..23
        HoraHasta    TINYINT       NOT NULL,       -- exclusivo
        Factor       DECIMAL(4,3)  NOT NULL,       -- 0.82 = 82% de los presentes
        Origen       NVARCHAR(20)  NOT NULL
            CONSTRAINT DF_Plan_Disp_Origen DEFAULT ('medido'),   -- medido | manual
        Muestras     INT           NULL,           -- intervalos usados en la medición
        ActualizadoEn DATETIME2(0) NOT NULL
            CONSTRAINT DF_Plan_Disp_Fecha DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT UQ_Plan_Disp UNIQUE (CampanaID, PoolID, DiaSemana, HoraDesde),
        CONSTRAINT CK_Plan_Disp_Factor CHECK (Factor > 0 AND Factor <= 1),
        CONSTRAINT CK_Plan_Disp_Dia    CHECK (DiaSemana BETWEEN 0 AND 7),
        CONSTRAINT CK_Plan_Disp_Horas  CHECK (HoraDesde BETWEEN 0 AND 23 AND HoraHasta BETWEEN 1 AND 24 AND HoraHasta > HoraDesde),
        CONSTRAINT CK_Plan_Disp_Origen CHECK (Origen IN ('medido', 'manual')),
        CONSTRAINT FK_Plan_Disp_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID)
    );
    PRINT 'planificacion.Disponibilidad creada.';
END
ELSE PRINT 'planificacion.Disponibilidad ya existía.';
GO

/* ====================================================================
   INSUMOS
   ==================================================================== */

/* -------------------------------------------------------------- eventos */
/* Calendario de días atípicos. Nace vacío y se siembra con la detección
   automática sobre el histórico (Origen = 'auto'); Operaciones carga a mano los que
   ya sabe, incluidos los FUTUROS, que son los únicos que el modelo no puede
   descubrir solo. `ExcluirDeEntrenamiento` es la otra mitad: un día de corte
   masivo no se puede usar para aprender el comportamiento normal. */
IF OBJECT_ID('planificacion.Evento', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Evento (
        EventoID     INT            IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_Evento PRIMARY KEY,
        CampanaID    INT            NULL,          -- NULL = afecta a todas
        Desde        DATETIME2(0)   NOT NULL,
        Hasta        DATETIME2(0)   NOT NULL,
        Tipo         NVARCHAR(40)   NOT NULL,      -- corte, tormenta, ola_calor, facturacion, otro
        Descripcion  NVARCHAR(400)  NULL,

        -- Cuánto multiplicó (o se espera que multiplique) el volumen normal.
        -- 2.5 = dos veces y media lo habitual. Lo calcula la detección y se puede
        -- corregir a mano.
        Factor       DECIMAL(6,3)   NULL,

        Origen       NVARCHAR(20)   NOT NULL
            CONSTRAINT DF_Plan_Evento_Origen DEFAULT ('manual'),   -- auto | manual
        ExcluirDeEntrenamiento BIT   NOT NULL
            CONSTRAINT DF_Plan_Evento_Excluir DEFAULT (1),
        Confirmado   BIT            NOT NULL
            CONSTRAINT DF_Plan_Evento_Confirmado DEFAULT (0),

        CreadoPor    INT            NULL,
        CreadoEn     DATETIME2(0)   NOT NULL
            CONSTRAINT DF_Plan_Evento_Fecha DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT CK_Plan_Evento_Rango  CHECK (Hasta > Desde),
        CONSTRAINT CK_Plan_Evento_Origen CHECK (Origen IN ('auto', 'manual')),
        CONSTRAINT CK_Plan_Evento_Factor CHECK (Factor IS NULL OR Factor > 0)
    );
    CREATE INDEX IX_Plan_Evento_rango ON planificacion.Evento (Desde, Hasta) INCLUDE (CampanaID, Tipo, Factor);
    PRINT 'planificacion.Evento creada.';
END
ELSE PRINT 'planificacion.Evento ya existía.';
GO

/* --------------------------------------------------------------- ajustes */
/* Override manual del planificador: "esta semana sumale 15% a Comercial porque
   sale la factura nueva". Es deliberado que exista desde el día uno: en WFM el
   que planifica siempre sabe cosas que el modelo no, y si no tiene dónde
   cargarlas termina planificando en un Excel paralelo y la herramienta muere. */
IF OBJECT_ID('planificacion.Ajuste', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Ajuste (
        AjusteID    INT            IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_Ajuste PRIMARY KEY,
        CampanaID   INT            NOT NULL,
        SkillID     INT            NULL,           -- NULL = toda la campaña
        Desde       DATETIME2(0)   NOT NULL,
        Hasta       DATETIME2(0)   NOT NULL,
        Factor      DECIMAL(6,3)   NOT NULL,       -- 1.15 = 15% más
        Motivo      NVARCHAR(400)  NOT NULL,       -- obligatorio: un override sin motivo no se audita
        Activo      BIT            NOT NULL
            CONSTRAINT DF_Plan_Ajuste_Activo DEFAULT (1),
        CreadoPor   INT            NULL,
        CreadoEn    DATETIME2(0)   NOT NULL
            CONSTRAINT DF_Plan_Ajuste_Fecha DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT CK_Plan_Ajuste_Rango  CHECK (Hasta > Desde),
        CONSTRAINT CK_Plan_Ajuste_Factor CHECK (Factor > 0 AND Factor <= 10),
        CONSTRAINT FK_Plan_Ajuste_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID)
    );
    CREATE INDEX IX_Plan_Ajuste_rango ON planificacion.Ajuste (CampanaID, Desde, Hasta) WHERE Activo = 1;
    PRINT 'planificacion.Ajuste creada.';
END
ELSE PRINT 'planificacion.Ajuste ya existía.';
GO

/* ====================================================================
   RESULTADOS
   ==================================================================== */

/* --------------------------------------------------------------- corrida */
/* Una corrida = un recálculo completo. Se versiona en vez de pisarse para poder
   contestar "¿qué habíamos pronosticado para hoy hace dos semanas?", que es la
   única forma honesta de medir si el modelo mejora. */
IF OBJECT_ID('planificacion.Corrida', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Corrida (
        CorridaID     BIGINT         IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_Corrida PRIMARY KEY,
        CampanaID     INT            NOT NULL,
        Horizonte     NVARCHAR(20)   NOT NULL,     -- operativo | presupuesto
        Desde         DATE           NOT NULL,
        Hasta         DATE           NOT NULL,
        Modelo        NVARCHAR(60)   NULL,         -- qué generó el volumen
        Estado        NVARCHAR(20)   NOT NULL
            CONSTRAINT DF_Plan_Corrida_Estado DEFAULT ('EN_CURSO'),
        Metricas      NVARCHAR(MAX)  NULL,         -- JSON: WAPE, cobertura, avisos
        Motivo        NVARCHAR(400)  NULL,
        EsVigente     BIT            NOT NULL
            CONSTRAINT DF_Plan_Corrida_Vigente DEFAULT (0),
        CreadoPor     INT            NULL,
        CreadoEn      DATETIME2(0)   NOT NULL
            CONSTRAINT DF_Plan_Corrida_Fecha DEFAULT (SYSUTCDATETIME()),
        TerminadoEn   DATETIME2(0)   NULL,

        CONSTRAINT CK_Plan_Corrida_Estado    CHECK (Estado IN ('EN_CURSO', 'OK', 'ERROR')),
        CONSTRAINT CK_Plan_Corrida_Horizonte CHECK (Horizonte IN ('operativo', 'presupuesto')),
        CONSTRAINT CK_Plan_Corrida_Rango     CHECK (Hasta >= Desde),
        CONSTRAINT FK_Plan_Corrida_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID)
    );
    -- Una sola corrida vigente por campaña y horizonte: es la que lee la pantalla.
    CREATE UNIQUE INDEX UX_Plan_Corrida_vigente
        ON planificacion.Corrida (CampanaID, Horizonte) WHERE EsVigente = 1;
    PRINT 'planificacion.Corrida creada.';
END
ELSE PRINT 'planificacion.Corrida ya existía.';
GO

/* ------------------------------------------------------------ pronóstico */
IF OBJECT_ID('planificacion.Pronostico', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Pronostico (
        CorridaID      BIGINT        NOT NULL,
        SkillID        INT           NOT NULL,
        Intervalo      DATETIME2(0)  NOT NULL,

        -- Demanda total del cliente (todos los contact centers). Cuando todavía no
        -- tengamos el 100% de las llamadas queda NULL y solo se usa LlamadasAcme.
        LlamadasTotal  DECIMAL(10,2) NULL,
        -- Porcentaje de asignación aplicado para llegar a lo nuestro.
        Asignacion     DECIMAL(5,4)  NULL,
        -- Lo que esperamos recibir nosotros, ya con ajustes manuales aplicados.
        LlamadasAcme   DECIMAL(10,2) NOT NULL,
        -- Lo mismo antes de los overrides, para poder mostrar cuánto pesó la mano.
        LlamadasBase   DECIMAL(10,2) NULL,

        TmoSeg         DECIMAL(8,2)  NOT NULL,

        CONSTRAINT PK_Plan_Pronostico PRIMARY KEY (CorridaID, SkillID, Intervalo),
        CONSTRAINT CK_Plan_Pronostico_Llamadas CHECK (LlamadasAcme >= 0),
        CONSTRAINT CK_Plan_Pronostico_Tmo      CHECK (TmoSeg >= 0),
        CONSTRAINT FK_Plan_Pronostico_Corrida FOREIGN KEY (CorridaID)
            REFERENCES planificacion.Corrida (CorridaID) ON DELETE CASCADE
    );
    PRINT 'planificacion.Pronostico creada.';
END
ELSE PRINT 'planificacion.Pronostico ya existía.';
GO

/* --------------------------------------------------------- requerimiento */
IF OBJECT_ID('planificacion.Requerimiento', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Requerimiento (
        CorridaID           BIGINT        NOT NULL,
        PoolID              INT           NOT NULL,
        Intervalo           DATETIME2(0)  NOT NULL,

        Llamadas            DECIMAL(10,2) NOT NULL,
        TmoSeg              DECIMAL(8,2)  NOT NULL,
        Trafico             DECIMAL(10,3) NOT NULL,   -- Erlangs

        OperadoresLinea     SMALLINT      NOT NULL,   -- los que tienen que estar atendiendo
        OperadoresPlanificar SMALLINT     NOT NULL,   -- los anteriores + shrinkage

        -- Las dos lecturas del nivel de servicio. NdsAtendidas es la que va a
        -- reportar el sistema de Voltara; NdsEntrantes es la que refleja lo que
        -- vivió el cliente. La brecha entre las dos es el abandono.
        NdsContractual      DECIMAL(5,4)  NULL,       -- Erlang C, el del contrato
        NdsAtendidas        DECIMAL(5,4)  NULL,
        NdsEntrantes        DECIMAL(5,4)  NULL,
        Abandono            DECIMAL(5,4)  NULL,
        Ocupacion           DECIMAL(5,4)  NULL,

        Motivo              NVARCHAR(40)  NULL,       -- qué restricción fijó el número

        CONSTRAINT PK_Plan_Requerimiento PRIMARY KEY (CorridaID, PoolID, Intervalo),
        CONSTRAINT FK_Plan_Req_Corrida FOREIGN KEY (CorridaID)
            REFERENCES planificacion.Corrida (CorridaID) ON DELETE CASCADE,
        CONSTRAINT FK_Plan_Req_Pool FOREIGN KEY (PoolID)
            REFERENCES planificacion.Pool (PoolID)
    );
    PRINT 'planificacion.Requerimiento creada.';
END
ELSE PRINT 'planificacion.Requerimiento ya existía.';
GO

/* ====================================================================
   PERMISOS
   ==================================================================== */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'planificador.view')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('planificador.view',
            'Ver el Planificador: pronóstico de llamadas por intervalo y operadores necesarios por pool');
    PRINT 'Permiso planificador.view creado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'planificador.edit')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('planificador.edit',
            'Configurar el Planificador y recalcular: objetivos por skill, pools, porcentaje de asignación del cliente, shrinkage, calendario de eventos y ajustes manuales del pronóstico');
    PRINT 'Permiso planificador.edit creado.';
END
GO

/* Ambos nacen SIN ASIGNAR, igual que audit:cuotas y audit:scheduler. Mientras no
   se asignen, la pantalla no la ve nadie salvo el super admin.

DECLARE @roles TABLE (name NVARCHAR(100));
INSERT INTO @roles (name) VALUES ('Gerente de Operaciones'), ('Planificación');   -- ajustar

INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT r.id, p.id
FROM pagina_web.Roles r
CROSS JOIN pagina_web.Permissions p
JOIN @roles x ON x.name = r.name
WHERE p.code IN ('planificador.view', 'planificador.edit')
  AND NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id);
--------------------------------------------------------------------------- */

/* ====================================================================
   SIEMBRA — Voltara (CampanaID 20)
   Números medidos sobre agosto de 2026, no inventados. Ver el encabezado.
   ==================================================================== */

IF NOT EXISTS (SELECT 1 FROM planificacion.Campana WHERE CampanaID = 20)
BEGIN
    INSERT INTO planificacion.Campana
        (CampanaID, Activa, IntervaloMin, MaxOcupacion, ShrinkageDefault, PacienciaSeg, Nota)
    VALUES
        (20, 1, 30, 0.850, 0.300, 853,
         'Paciencia 853s = MLE exponencial censurado sobre jun-ago 2026 (6.207 abandonos / 5.293.958s de espera acumulada en Emergencias).');
    PRINT 'Campaña Voltara sembrada.';
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Pool WHERE CampanaID = 20)
BEGIN
    INSERT INTO planificacion.Pool (CampanaID, Nombre, MinOperadores, Nota) VALUES
        (20, 'General', 2,
         'Emergencias, Comercial, TOC, Electrodependientes y las dos colas empresariales: ~170 operadores, 98% del volumen. Cada uno atiende entre 3 y 5 colas.'),
        (20, 'Reclamos y Grandes Cuentas', 1,
         'Grupo chico: 8 operadores en agosto 2026, con 16-17% de su carga en estas dos colas.'),
        (20, 'Comercial Consumo', 1,
         'Único skill con dedicación exclusiva: 6 operadores, 100% de su carga acá. 189 llamadas en agosto 2026.');
    PRINT 'Pools de Voltara sembrados.';
END
GO

IF NOT EXISTS (SELECT 1 FROM planificacion.Skill WHERE CampanaID = 20)
BEGIN
    DECLARE @general INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 20 AND Nombre = 'General');
    DECLARE @chico   INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 20 AND Nombre = 'Reclamos y Grandes Cuentas');
    DECLARE @consumo INT = (SELECT PoolID FROM planificacion.Pool WHERE CampanaID = 20 AND Nombre = 'Comercial Consumo');

    INSERT INTO planificacion.Skill
        (CampanaID, SkillID, Nombre, PoolID, ObjetivoNds, UmbralSeg, MaxAbandono, Nota)
    VALUES
        (20,  1, 'CNR',                     @general, 0.800, 20, NULL, NULL),
        (20,  2, 'TOC',                     @general, 0.800, 20, NULL, NULL),
        (20,  3, 'CNR-EMPRESARIAL',         @general, 0.800, 20, NULL, NULL),
        (20,  4, 'COMERCIAL',               @general, 0.800, 20, NULL,
             'Agosto 2026: 69,8% a 20s. No cumple.'),
        (20,  5, 'ELECTRODEPENDIENTES',     @general, 0.800, 20, 0.0050,
             'Compromiso de 100% de nivel de atención: manda el techo de abandono, no el NDS. El 0,5% NO es una rebaja del compromiso: en una cola el cero exacto es inalcanzable (pediría dotación infinita), así que se fija el techo operativo más exigente que sí se puede sostener. Referencia: jun-ago 2026 cerró en 0,67% de abandono (14 de 2.087) y agosto en 98,1% de atención.'),
        (20,  6, 'EMERGENCIAS',             @general, 0.800, 20, NULL,
             '67% del volumen, TMO 180s (ponderado por llamadas). Agosto 2026: 86,3% a 20s, cumple.'),
        (20,  7, 'EMERGENCIAS-EMPRESARIAL', @general, 0.800, 20, NULL, NULL),
        (20,  8, 'GRANDES-CUENTAS',         @chico,   0.800, 20, NULL,
             'Agosto 2026: 36,9% a 20s. Es el peor de todos.'),
        (20,  9, 'RECLAMO-DANO',            @chico,   0.800, 20, NULL,
             'Agosto 2026: 45,6% a 20s. No cumple.'),
        (20, 10, 'PEQUENOS-CLIENTES',       NULL,     NULL,  NULL, NULL, 'Sin volumen en 2026.'),
        (20, 11, 'CALLBACK',                NULL,     NULL,  NULL, NULL, 'Sin volumen en 2026.'),
        (20, 12, 'ATENCION-DE-DANO',        NULL,     NULL,  NULL, NULL, 'Sin volumen en 2026.'),
        (20, 13, 'COMERCIAL-CONSUMO',       @consumo, 0.800, 20, NULL,
             'Grupo cerrado de 6 operadores dedicados.');
    PRINT 'Skills de Voltara sembrados.';
END
GO

/* Disponibilidad medida el 12/08/2026 sobre el pool General. De 09 a 17 la cola
   ata y el factor se puede estimar; fuera de esa franja el NDS real satura en
   100% y la estimación no tiene información, así que se deja el turno noche en
   un valor conservador y se corrige cuando el recálculo junte más días. */
IF NOT EXISTS (SELECT 1 FROM planificacion.Disponibilidad WHERE CampanaID = 20)
BEGIN
    INSERT INTO planificacion.Disponibilidad
        (CampanaID, PoolID, DiaSemana, HoraDesde, HoraHasta, Factor, Origen, Muestras)
    VALUES
        (20, NULL, 0,  0,  8, 0.900, 'manual', NULL),
        (20, NULL, 0,  8, 17, 0.820, 'medido', 18),
        (20, NULL, 0, 17, 24, 0.900, 'manual', NULL);
    PRINT 'Disponibilidad de Voltara sembrada.';
END
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT p.Nombre AS Pool, s.SkillID, s.Nombre AS Skill,
       s.ObjetivoNds, s.UmbralSeg, s.MaxAbandono
FROM planificacion.Skill s
LEFT JOIN planificacion.Pool p ON p.PoolID = s.PoolID
WHERE s.CampanaID = 20
ORDER BY p.Nombre, s.SkillID;

SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('planificador.view', 'planificador.edit');
GO
