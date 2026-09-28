-- StoredProcedure [calidad].[sp_AsignarSkill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_AsignarSkill]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_AsignarSkill] AS' 
END
GO

ALTER PROCEDURE [calidad].[sp_AsignarSkill]
    @TargetCampanaID INT,
    @NombreSkill NVARCHAR(255)
AS
BEGIN
    SET NOCOUNT ON;

    -- Usamos una transacción para asegurar la integridad de la operación.
    BEGIN TRANSACTION;

    DECLARE @TargetEmpresaID INT;
    DECLARE @ExistingSkillID INT;

    -- 1. Validar la CampanaID y obtener su EmpresaID
    SELECT @TargetEmpresaID = EmpresaID
    FROM calidad.Campanas
    WHERE CampanaID = @TargetCampanaID;

    -- Si la campaña no existe, cancelamos todo.
    IF @TargetEmpresaID IS NULL
    BEGIN
        RAISERROR ('La CampanaID %d no existe. No se puede asignar el skill.', 16, 1, @TargetCampanaID);
        ROLLBACK TRANSACTION;
        RETURN;
    END

    -- 2. Buscar si el skill ya existe para esta EMPRESA (en cualquier campaña)
    -- Usamos TOP 1 y ORDER BY [IsActive] DESC por si existe un registro histórico inactivo y uno activo.
    SELECT TOP 1 @ExistingSkillID = s.SkillID
    FROM calidad.Skills AS s
    JOIN calidad.Campanas AS c ON s.CampanaID = c.CampanaID
    WHERE
        c.EmpresaID = @TargetEmpresaID
        AND s.Nombre = @NombreSkill
    ORDER BY s.[IsActive] DESC;

    -- 3. Decidir si crear o modificar
    BEGIN TRY
        IF @ExistingSkillID IS NOT NULL
        BEGIN
            -- CASO "MODIFICAR": El skill ya existe para esta empresa.
            -- Lo "movemos" a la nueva CampanaID y LO REACTIVAMOS por si era un soft delete.
            UPDATE calidad.Skills
            SET CampanaID = @TargetCampanaID,
                [IsActive] = 1  -- <-- CORRECCIÓN: Reactivación del soft delete
            WHERE SkillID = @ExistingSkillID;
            
            PRINT 'Skill existente (' + @NombreSkill + ') re-asignado y/o reactivado a la CampanaID ' + CAST(@TargetCampanaID AS VARCHAR);
        END
        ELSE
        BEGIN
            -- CASO "INGRESAR": El skill es nuevo para esta empresa.
            -- Lo creamos, asignamos a la CampanaID y aseguramos que nazca activo.
            INSERT INTO calidad.Skills (Nombre, CampanaID, [IsActive])
            VALUES (@NombreSkill, @TargetCampanaID, 1); -- <-- CORRECCIÓN: Nacimiento explícito como activo
            
            PRINT 'Nuevo skill (' + @NombreSkill + ') creado y asignado a la CampanaID ' + CAST(@TargetCampanaID AS VARCHAR);
        END

        -- Si todo fue bien, confirmamos los cambios.
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        -- Si algo falla, revertimos los cambios.
        ROLLBACK TRANSACTION;
        THROW; -- Re-lanzamos el error.
    END CATCH
END
GO
