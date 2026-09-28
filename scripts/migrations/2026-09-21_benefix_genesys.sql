/* ============================================================================
   2026-09-21 — Benefix en auditorías: llamados de Genesys Cloud
   ----------------------------------------------------------------------------
   Aditiva e idempotente. Aplicar ANTES de deployar el código que la usa
   (AuditorIA/SQL_query.get_filtered_data_Benefix y scripts/benefix_genesys.py).

   1. Esquema Benefix + Benefix.Interacciones: una fila por TRAMO DE AGENTE de
      cada conversación de Genesys (voz y mensajería), la carga
      scripts/benefix_genesys.py desde la API de analytics. Las conversaciones que
      no llegaron a un agente (IVR, abandonos) no se guardan: no hay nada que
      auditar. Horarios en hora local (UTC-3).
   2. Benefix.InteraccionesCargas: un renglón por día cargado (OK / VACIO / ERROR),
      para que el cargador sea reanudable, igual que el informe IVR de Enerval.
   3. Empresa "Benefix" + permiso template:benefix (nace SIN asignar a ningún rol)
      + campaña "Benefix" en la plataforma Genesys (PlataformaID 3) + las colas que
      atienden los operadores de Acme como skills de la campaña.

   Convive con dbo.[Benefix Interacciones] (la carga otro proceso, desde el export
   de la consola, al minuto y sin id de usuario ni de grabación): no se toca.
   ============================================================================ */
SET XACT_ABORT ON;
GO

IF SCHEMA_ID('Benefix') IS NULL
    EXEC('CREATE SCHEMA Benefix');
GO

IF OBJECT_ID('Benefix.Interacciones', 'U') IS NULL
BEGIN
    CREATE TABLE Benefix.Interacciones (
        ConversationId      varchar(36)    NOT NULL,
        ParticipantId       varchar(36)    NOT NULL,
        -- Día local del inicio de la conversación: es la unidad de carga (se borra
        -- y se vuelve a insertar el día entero).
        Fecha               date           NOT NULL,
        InicioConversacion  datetime2(0)   NOT NULL,
        FinConversacion     datetime2(0)   NULL,
        InicioAgente        datetime2(0)   NULL,
        FinAgente           datetime2(0)   NULL,
        Canal               varchar(20)    NOT NULL,   -- mediaType de Genesys: voice / message / ...
        Sentido             varchar(10)    NULL,       -- Entrante / Saliente
        UserId              varchar(36)    NULL,
        -- Nombre del usuario en Genesys ("ACME - Nombre Apellido"). Es el mismo string
        -- que dbo.usuarios.usuario, por el que se resuelve la persona de la auditoría.
        Operador            nvarchar(200)  NULL,
        Email               nvarchar(200)  NULL,
        QueueId             varchar(36)    NULL,
        Cola                nvarchar(200)  NULL,
        Skills              nvarchar(1000) NULL,
        WrapUpCodeId        varchar(36)    NULL,
        Tipificacion        nvarchar(300)  NULL,       -- nombre del wrap-up ("Conclusión")
        NotaWrapUp          nvarchar(4000) NULL,
        TelefonoCliente     varchar(64)    NULL,       -- ANI si entra, DNIS si sale
        ANI                 varchar(200)   NULL,
        DNIS                varchar(200)   NULL,
        SegundosAlerta      int            NULL,
        SegundosHablados    int            NULL,
        SegundosEspera      int            NULL,
        CantidadEsperas     int            NULL,
        SegundosACW         int            NULL,
        SegundosManejo      int            NULL,       -- hablado + espera + ACW
        Transferido         bit            NOT NULL CONSTRAINT DF_BenefixInteracciones_Transferido DEFAULT 0,
        -- disconnectType del último tramo de conversación del agente:
        -- peer = cortó el cliente, client/endpoint = cortó el operador, transfer...
        DesconexionAgente   varchar(30)    NULL,
        -- Genesys marcó la conversación como grabada (alguna sesión con recording=true).
        Grabada             bit            NOT NULL,
        AgentesEnConversacion tinyint      NOT NULL,
        CargadoEn           datetime2(0)   NOT NULL CONSTRAINT DF_BenefixInteracciones_CargadoEn DEFAULT SYSDATETIME(),
        CONSTRAINT PK_BenefixInteracciones PRIMARY KEY (ConversationId, ParticipantId)
    );

    CREATE INDEX IX_BenefixInteracciones_Fecha
        ON Benefix.Interacciones (Fecha, Canal)
        INCLUDE (InicioConversacion, Operador, Cola, Tipificacion, Sentido,
                 SegundosHablados, Grabada);
END
GO

IF OBJECT_ID('Benefix.InteraccionesCargas', 'U') IS NULL
    CREATE TABLE Benefix.InteraccionesCargas (
        Dia             date            NOT NULL CONSTRAINT PK_BenefixInteraccionesCargas PRIMARY KEY,
        Estado          varchar(10)     NOT NULL,   -- OK / VACIO / ERROR
        Conversaciones  int             NULL,       -- las que devolvió analytics para el día
        Filas           int             NULL,       -- tramos de agente insertados
        Segundos        decimal(9, 1)   NULL,
        Intentos        int             NOT NULL CONSTRAINT DF_BenefixCargas_Intentos DEFAULT 1,
        Error           nvarchar(1000)  NULL,
        FechaCarga      datetime2(0)    NOT NULL CONSTRAINT DF_BenefixCargas_FechaCarga DEFAULT SYSDATETIME()
    );
GO

/* ---------------------------------------------------------------------------
   Empresa, permiso, campaña y skills
   --------------------------------------------------------------------------- */
BEGIN TRANSACTION;

DECLARE @PermisoID int = (SELECT id FROM pagina_web.Permissions WHERE code = 'template:benefix');
IF @PermisoID IS NULL
BEGIN
    INSERT INTO pagina_web.Permissions (code, description, activo)
    VALUES ('template:benefix', 'Acceso a auditorías, dashboards y plantillas de Benefix', 0);
    SET @PermisoID = SCOPE_IDENTITY();
END

DECLARE @EmpresaID int = (SELECT EmpresaID FROM calidad.Empresas WHERE Nombre = 'Benefix');
IF @EmpresaID IS NULL
BEGIN
    INSERT INTO calidad.Empresas (Nombre, RequiredPermissionID) VALUES ('Benefix', @PermisoID);
    SET @EmpresaID = SCOPE_IDENTITY();
END

COMMIT TRANSACTION;
GO

DECLARE @EmpresaID int = (SELECT EmpresaID FROM calidad.Empresas WHERE Nombre = 'Benefix');
DECLARE @PlataformaGenesys int = (SELECT PlataformaID FROM calidad.Plataformas WHERE nombre = 'Genesys');

IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE EmpresaID = @EmpresaID AND Nombre = 'Benefix')
    -- sp_CrearCampana también corre sp_SincronizarPermisosEmpresas: activa el
    -- template:benefix ahora que la empresa tiene una campaña activa.
    EXEC calidad.sp_CrearCampana @NombreCampana = N'Benefix', @EmpresaID = @EmpresaID,
                                 @PlataformaID = @PlataformaGenesys;

DECLARE @CampanaID int = (SELECT CampanaID FROM calidad.Campanas
                          WHERE EmpresaID = @EmpresaID AND Nombre = 'Benefix');

-- Colas de voz de los operadores de Acme (medido 2026-09-14..20): "Benefix" para
-- los entrantes y "Saliente_Acme" para los salientes. Más colas se suman desde el
-- ABM de plantillas (la lista sale de Benefix.Interacciones).
IF NOT EXISTS (SELECT 1 FROM calidad.Skills WHERE CampanaID = @CampanaID AND Nombre = 'Benefix' AND IsActive = 1)
    EXEC calidad.sp_AsignarSkill @TargetCampanaID = @CampanaID, @NombreSkill = N'Benefix';
IF NOT EXISTS (SELECT 1 FROM calidad.Skills WHERE CampanaID = @CampanaID AND Nombre = 'Saliente_Acme' AND IsActive = 1)
    EXEC calidad.sp_AsignarSkill @TargetCampanaID = @CampanaID, @NombreSkill = N'Saliente_Acme';
GO

/* Verificación:
SELECT e.EmpresaID, e.Nombre, p.code, p.activo, c.CampanaID, c.PlataformaID, s.Nombre AS Skill
FROM calidad.Empresas e
JOIN pagina_web.Permissions p ON p.id = e.RequiredPermissionID
LEFT JOIN calidad.Campanas c ON c.EmpresaID = e.EmpresaID
LEFT JOIN calidad.Skills s ON s.CampanaID = c.CampanaID AND s.IsActive = 1
WHERE e.Nombre = 'Benefix';
*/
