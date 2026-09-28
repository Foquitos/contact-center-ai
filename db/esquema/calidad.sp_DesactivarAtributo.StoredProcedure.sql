-- StoredProcedure [calidad].[sp_DesactivarAtributo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_DesactivarAtributo]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_DesactivarAtributo] AS' 
END
GO
ALTER PROCEDURE [calidad].[sp_DesactivarAtributo] @TargetAtributoID INT AS BEGIN SET NOCOUNT ON;
    UPDATE calidad.Atributos SET IsActive = 0 WHERE AtributoID = @TargetAtributoID AND IsActive = 1;
    PRINT 'Atributo ' + CAST(@TargetAtributoID AS VARCHAR) + ' desactivado.';
END;
GO
