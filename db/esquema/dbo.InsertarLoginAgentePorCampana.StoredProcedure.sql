-- StoredProcedure [dbo].[InsertarLoginAgentePorCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[InsertarLoginAgentePorCampana]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[InsertarLoginAgentePorCampana] AS' 
END
GO

ALTER PROCEDURE [dbo].[InsertarLoginAgentePorCampana]
    @LoginId VARCHAR(MAX),
    @campana_ejemplo VARCHAR(MAX),  -- Nombre de campaña existente
    @campana_nueva VARCHAR(MAX),    -- Nombre de campaña nueva
	@desde DATE,
	@hasta DATE
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @idCampanaEjemplo INT;
    DECLARE @idCampanaNueva INT;

    -- Obtener IDs de campañas
    SELECT @idCampanaEjemplo = ca.idCampania
    FROM campanas_empresa_mitrol ca
    WHERE ca.Campaña = @campana_ejemplo;

    SELECT @idCampanaNueva = ca.idCampania
    FROM campanas_empresa_mitrol ca
    WHERE ca.Campaña = @campana_nueva;

    -- Insertar en login_agentes_por_campanas
    INSERT INTO login_agentes_por_campanas
           (Fecha,
            LoginId,
            LogIn,
            LogOut,
            idAgente,
            idGrupo,
            [id Campaña],
            Tiempo,
            Logueado)
    SELECT 
           lo.Fecha,
           lo.LoginId,
           lo.LogIn,
           lo.LogOut,
           idAgente,
           idGrupo,
           @idCampanaNueva AS [id Campaña],	-- Id campaña a insertar
           lo.Tiempo,
           lo.Logueado
    FROM login_agentes_por_campanas lo
    WHERE lo.LoginId = @LoginId
      AND lo.Fecha BETWEEN @desde AND @hasta
      AND lo.[id Campaña] = @idCampanaEjemplo;	-- Id campaña a copiar
END;
GO
