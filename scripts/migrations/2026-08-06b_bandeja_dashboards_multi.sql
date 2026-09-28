/* ============================================================================
   Feature — Varios DASHBOARDS por plantilla (perfiles de visualización)
   Fecha: 2026-08-06 (b)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Reemplaza la config única por plantilla (calidad.BandejaVizConfig) por una
   tabla de PERFILES: una plantilla puede tener VARIOS dashboards con distinto
   criterio de visualización. Ej: un perfil "Cliente" (resumido, sin internos),
   otro "Operaciones" (con todo el detalle), otro "Gerencia".

   calidad.BandejaDashboard:
     - PlantillaID  : a qué plantilla pertenece el perfil.
     - Nombre       : nombre del perfil ("Cliente", "Operaciones", …).
     - EsDefault    : cuál se abre por defecto (uno solo por plantilla).
     - Orden        : orden en el selector.
     - Config       : JSON con la config (mismo esquema por-atributo de antes,
                      + claves a nivel dashboard que se irán sumando: orden de
                      atributos, secciones, KPIs, etc.).

   Este archivo SUPERSEDE a 2026-08-06_bandeja_viz_config.sql. Es idempotente y
   autocontenido (crea también el permiso bandeja.config por si no se corrió la
   migración previa). Si la tabla vieja existe, migra sus filas como un perfil
   "Principal" (default) y la elimina.

   CÓMO CORRER: contra la BD Acme, ANTES del deploy. Con correr SOLO este archivo
   alcanza (no hace falta el 2026-08-06 previo).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --- 1) Tabla de perfiles de dashboard ----------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name = 'calidad' AND t.name = 'BandejaDashboard'
)
BEGIN
    CREATE TABLE calidad.BandejaDashboard (
        Id             INT IDENTITY(1,1) NOT NULL,
        PlantillaID    INT           NOT NULL,
        Nombre         NVARCHAR(120) NOT NULL,
        EsDefault      BIT           NOT NULL CONSTRAINT DF_BandejaDashboard_Def DEFAULT 0,
        Orden          INT           NOT NULL CONSTRAINT DF_BandejaDashboard_Ord DEFAULT 0,
        Config         NVARCHAR(MAX) NOT NULL
            CONSTRAINT CK_BandejaDashboard_Json CHECK (ISJSON(Config) = 1),
        ActualizadoPor INT           NULL,
        ActualizadoEn  DATETIME2     NOT NULL CONSTRAINT DF_BandejaDashboard_Fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_BandejaDashboard PRIMARY KEY (Id),
        CONSTRAINT UQ_BandejaDashboard_Plantilla_Nombre UNIQUE (PlantillaID, Nombre)
    );
    CREATE INDEX IX_BandejaDashboard_Plantilla ON calidad.BandejaDashboard (PlantillaID);
END
GO

/* --- 2) Permiso de edición (por si no corrió la migración previa) --------- */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'bandeja.config')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('bandeja.config',
            'Editar los dashboards/visualización del Dashboard de Auditorías (perfiles, gráficos, colores, metas y umbrales por atributo)');
END
GO

/* --- 3) Migrar la config única vieja (si existe) como perfil "Principal" --- */
IF OBJECT_ID('calidad.BandejaVizConfig', 'U') IS NOT NULL
BEGIN
    INSERT INTO calidad.BandejaDashboard (PlantillaID, Nombre, EsDefault, Orden, Config, ActualizadoPor, ActualizadoEn)
    SELECT v.PlantillaID, N'Principal', 1, 0, v.Config, v.ActualizadoPor, v.ActualizadoEn
    FROM calidad.BandejaVizConfig v
    WHERE NOT EXISTS (
        SELECT 1 FROM calidad.BandejaDashboard d WHERE d.PlantillaID = v.PlantillaID
    );

    DROP TABLE calidad.BandejaVizConfig;
END
GO

SELECT TOP 20 Id, PlantillaID, Nombre, EsDefault, Orden FROM calidad.BandejaDashboard ORDER BY PlantillaID, Orden;
GO
