-- StoredProcedure [calidad].[sp_SincronizarPermisosEmpresas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_SincronizarPermisosEmpresas]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_SincronizarPermisosEmpresas] AS' 
END
GO

/* ================ 2) Sincronizador de permisos por empresa ================= */

ALTER   PROCEDURE [calidad].[sp_SincronizarPermisosEmpresas]
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
