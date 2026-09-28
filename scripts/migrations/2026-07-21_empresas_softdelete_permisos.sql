/* ============================================================================
   Feature — Soft delete de empresas y baja del permiso template:<empresa>
   Fecha: 2026-07-21
   Autor: equipo Acme

   POR QUÉ
   -------
   calidad.Empresas ya tenía IsActive, pero NADIE lo filtraba: las empresas
   desactivadas (Brandsync, Autoone, Movilink, Osrural, RRHH) seguían
   apareciendo en el selector de Plantillas y dentro del alcance por empresa.
   Además el permiso template:<empresa> quedaba vivo en Gestionar Roles aunque
   la empresa ya no existiera operativamente.

   QUÉ AGREGA
   ----------
   1) pagina_web.Permissions.activo (BIT NOT NULL DEFAULT 1): un permiso
      inactivo NO se ofrece en Gestionar Roles y NO otorga nada. Las filas de
      RolePermissions se conservan intactas, así que reactivar la empresa
      devuelve el permiso a los roles que ya lo tenían.
   2) calidad.sp_SincronizarPermisosEmpresas: recalcula activo para TODOS los
      permisos referenciados por calidad.Empresas.RequiredPermissionID.
      Regla: el permiso queda activo si existe al menos una empresa activa que
      lo referencie Y esa empresa tiene al menos una campaña activa.
      - Solo toca permisos referenciados por alguna empresa: los transversales
        (template:read, template:create, roles:manage, …) nunca se tocan.
      - Contempla permisos compartidos: template:hidra lo usan HIDRA (1) y HIDRA
        Comercial (14); solo baja si las DOS dejan de calificar.
   3) calidad.sp_DesactivarEmpresa / sp_ReactivarEmpresa: soft delete con
      cascada (campañas -> plantillas -> atributos, y skills) + sincronización
      del permiso.
   4) sp_DesactivarCampana y sp_CrearCampana ahora sincronizan los permisos:
      desactivar la última campaña activa de una empresa baja el permiso, y
      crear una campaña lo revive.
   5) Backfill: aplica la cascada a las empresas que ya estaban en IsActive = 0
      y deja los permisos sincronizados.

   OJO CON LA REACTIVACIÓN
   -----------------------
   sp_ReactivarEmpresa reactiva TODO lo que cuelga de la empresa. Si alguna
   campaña/plantilla estaba dada de baja individualmente antes de desactivar
   la empresa, vuelve activa: hay que volver a bajarla con sp_DesactivarCampana
   / sp_DesactivarPlantilla. Es el precio de que reactivar sea un solo paso.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme ANTES de deployar el código nuevo (el código
   viejo no lee la columna). Idempotente.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ================ 1) Permissions.activo ==================================== */

IF COL_LENGTH('pagina_web.Permissions', 'activo') IS NULL
BEGIN
    ALTER TABLE pagina_web.Permissions
        ADD activo BIT NOT NULL CONSTRAINT DF_Permissions_activo DEFAULT 1;
END
GO

/* ================ 2) Sincronizador de permisos por empresa ================= */

CREATE OR ALTER PROCEDURE calidad.sp_SincronizarPermisosEmpresas
AS
BEGIN
    SET NOCOUNT ON;

    /* Un permiso template:<empresa> vale solo si hay algo que auditar detrás:
       empresa activa CON al menos una campaña activa. El MAX() por permiso
       resuelve el caso de varias empresas compartiendo el mismo permiso: basta
       con que UNA califique.

       La CTE calcula el "califica" por empresa y recién después se agrega:
       SQL Server no admite MAX() sobre una expresión que contenga un subquery
       (error 130), así que el EXISTS no puede ir adentro del MAX. */
    WITH empresa_estado AS (
        SELECT e.RequiredPermissionID AS permission_id,
               CASE WHEN e.IsActive = 1
                         AND EXISTS (SELECT 1
                                       FROM calidad.Campanas c
                                      WHERE c.EmpresaID = e.EmpresaID
                                        AND c.IsActive = 1)
                    THEN 1 ELSE 0 END AS califica
          FROM calidad.Empresas e
         WHERE e.RequiredPermissionID IS NOT NULL
    )
    UPDATE p
       SET p.activo = v.deberia
      FROM pagina_web.Permissions p
      JOIN (
            SELECT permission_id, CAST(MAX(califica) AS BIT) AS deberia
              FROM empresa_estado
             GROUP BY permission_id
           ) v ON v.permission_id = p.id
     WHERE p.activo <> v.deberia;
END
GO

/* ================ 3) Alta/baja de empresas ================================= */

CREATE OR ALTER PROCEDURE calidad.sp_DesactivarEmpresa
    @TargetEmpresaID INT
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM calidad.Empresas WHERE EmpresaID = @TargetEmpresaID)
    BEGIN
        RAISERROR ('El EmpresaID %d no existe en calidad.Empresas.', 16, 1, @TargetEmpresaID);
        RETURN;
    END

    BEGIN TRANSACTION;
    BEGIN TRY
        UPDATE calidad.Empresas
           SET IsActive = 0
         WHERE EmpresaID = @TargetEmpresaID AND IsActive = 1;

        /* Cascada: campañas -> plantillas -> atributos, y skills. */
        UPDATE a
           SET a.IsActive = 0
          FROM calidad.Atributos a
          JOIN calidad.Plantillas pl ON pl.PlantillaID = a.PlantillaID
          JOIN calidad.Campanas   c  ON c.CampanaID    = pl.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND a.IsActive = 1;

        UPDATE pl
           SET pl.IsActive = 0
          FROM calidad.Plantillas pl
          JOIN calidad.Campanas c ON c.CampanaID = pl.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND pl.IsActive = 1;

        UPDATE s
           SET s.IsActive = 0
          FROM calidad.Skills s
          JOIN calidad.Campanas c ON c.CampanaID = s.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND s.IsActive = 1;

        UPDATE calidad.Campanas
           SET IsActive = 0
         WHERE EmpresaID = @TargetEmpresaID AND IsActive = 1;

        EXEC calidad.sp_SincronizarPermisosEmpresas;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO

CREATE OR ALTER PROCEDURE calidad.sp_ReactivarEmpresa
    @TargetEmpresaID INT
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM calidad.Empresas WHERE EmpresaID = @TargetEmpresaID)
    BEGIN
        RAISERROR ('El EmpresaID %d no existe en calidad.Empresas.', 16, 1, @TargetEmpresaID);
        RETURN;
    END

    BEGIN TRANSACTION;
    BEGIN TRY
        UPDATE calidad.Empresas
           SET IsActive = 1
         WHERE EmpresaID = @TargetEmpresaID AND IsActive = 0;

        /* Espejo exacto de la baja: reactiva TODO lo que cuelga de la empresa.
           Ver "OJO CON LA REACTIVACIÓN" en la cabecera del archivo. */
        UPDATE calidad.Campanas
           SET IsActive = 1
         WHERE EmpresaID = @TargetEmpresaID AND IsActive = 0;

        UPDATE pl
           SET pl.IsActive = 1
          FROM calidad.Plantillas pl
          JOIN calidad.Campanas c ON c.CampanaID = pl.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND pl.IsActive = 0;

        UPDATE a
           SET a.IsActive = 1
          FROM calidad.Atributos a
          JOIN calidad.Plantillas pl ON pl.PlantillaID = a.PlantillaID
          JOIN calidad.Campanas   c  ON c.CampanaID    = pl.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND a.IsActive = 0;

        UPDATE s
           SET s.IsActive = 1
          FROM calidad.Skills s
          JOIN calidad.Campanas c ON c.CampanaID = s.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND s.IsActive = 0;

        EXEC calidad.sp_SincronizarPermisosEmpresas;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO

/* ================ 4) Enganche en el ciclo de vida de campañas ============== */
/* Idéntico al original + la sincronización del permiso al final: si esta era
   la última campaña activa de la empresa, el permiso deja de tener sentido. */

CREATE OR ALTER PROCEDURE calidad.sp_DesactivarCampana
    @TargetCampanaID INT
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE calidad.Campanas   SET IsActive = 0 WHERE CampanaID = @TargetCampanaID AND IsActive = 1;
    UPDATE calidad.Plantillas SET IsActive = 0 WHERE CampanaID = @TargetCampanaID AND IsActive = 1;
    UPDATE calidad.Skills     SET IsActive = 0 WHERE CampanaID = @TargetCampanaID AND IsActive = 1;

    EXEC calidad.sp_SincronizarPermisosEmpresas;
END
GO

/* Crear una campaña puede revivir el permiso de una empresa que se había
   quedado sin campañas activas. */
CREATE OR ALTER PROCEDURE calidad.sp_CrearCampana
    @NombreCampana NVARCHAR(255),
    @EmpresaID INT,
    @PlataformaID INT
AS
BEGIN
    SET NOCOUNT ON;

    -- 1. Validaciones Previas
    IF NOT EXISTS (SELECT 1 FROM calidad.Empresas WHERE EmpresaID = @EmpresaID)
    BEGIN
        RAISERROR ('El EmpresaID %d no existe en la tabla calidad.Empresas.', 16, 1, @EmpresaID);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM calidad.Plataformas WHERE PlataformaID = @PlataformaID)
    BEGIN
        RAISERROR ('El PlataformaID %d no existe en la tabla calidad.Plataformas.', 16, 1, @PlataformaID);
        RETURN;
    END

    IF EXISTS (SELECT 1 FROM calidad.Campanas WHERE Nombre = @NombreCampana AND EmpresaID = @EmpresaID)
    BEGIN
        RAISERROR ('Ya existe una campaña con el nombre "%s" para la EmpresaID %d.', 16, 1, @NombreCampana, @EmpresaID);
        RETURN;
    END

    BEGIN TRANSACTION;

    DECLARE @NuevaCampanaID INT;

    BEGIN TRY
        INSERT INTO calidad.Campanas (Nombre, EmpresaID, PlataformaID)
        VALUES (@NombreCampana, @EmpresaID, @PlataformaID);

        SET @NuevaCampanaID = SCOPE_IDENTITY();

        EXEC calidad.sp_SincronizarPermisosEmpresas;

        COMMIT TRANSACTION;

        SELECT @NuevaCampanaID AS NuevaCampanaID;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO

/* ================ 5) Backfill ============================================== */
/* Las empresas ya desactivadas nunca cascadearon: sus campañas/plantillas
   siguen en IsActive = 1. Se les aplica la baja completa. */

DECLARE @EmpresaID INT;
DECLARE empresas_inactivas CURSOR LOCAL FAST_FORWARD FOR
    SELECT EmpresaID FROM calidad.Empresas WHERE IsActive = 0;

OPEN empresas_inactivas;
FETCH NEXT FROM empresas_inactivas INTO @EmpresaID;
WHILE @@FETCH_STATUS = 0
BEGIN
    EXEC calidad.sp_DesactivarEmpresa @TargetEmpresaID = @EmpresaID;
    FETCH NEXT FROM empresas_inactivas INTO @EmpresaID;
END
CLOSE empresas_inactivas;
DEALLOCATE empresas_inactivas;
GO

EXEC calidad.sp_SincronizarPermisosEmpresas;
GO

/* ================ REPORTE ================================================== */

SELECT 'permisos inactivos (esperado 5: brandsync, autoone, movilink, osrural, rrhh)' AS chequeo,
       COUNT(*) AS filas
  FROM pagina_web.Permissions WHERE activo = 0
UNION ALL
SELECT 'campanas activas de empresas inactivas (esperado 0)', COUNT(*)
  FROM calidad.Campanas c
  JOIN calidad.Empresas e ON e.EmpresaID = c.EmpresaID
 WHERE e.IsActive = 0 AND c.IsActive = 1
UNION ALL
SELECT 'permisos activos de empresas activas (esperado 11 empresas / 10 permisos)', COUNT(DISTINCT p.id)
  FROM pagina_web.Permissions p
  JOIN calidad.Empresas e ON e.RequiredPermissionID = p.id
 WHERE e.IsActive = 1 AND p.activo = 1;
GO

SELECT p.id, p.code, p.activo,
       STRING_AGG(e.Nombre + CASE WHEN e.IsActive = 1 THEN ' (activa)' ELSE ' (inactiva)' END, ', ') AS empresas
  FROM pagina_web.Permissions p
  JOIN calidad.Empresas e ON e.RequiredPermissionID = p.id
 GROUP BY p.id, p.code, p.activo
 ORDER BY p.activo, p.code;
GO
