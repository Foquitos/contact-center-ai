-- StoredProcedure [dbo].[AuroraSalud_Clientes_Rellamar]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[AuroraSalud_Clientes_Rellamar]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[AuroraSalud_Clientes_Rellamar] AS' 
END
GO
ALTER PROCEDURE [dbo].[AuroraSalud_Clientes_Rellamar]
    @fecha DATE
AS
BEGIN
    SET NOCOUNT ON;

    WITH ClientesNormalizados AS (
        -- Normalizamos los números de WhatsApp eliminando el prefijo y el guión bajo
        SELECT 
            CASE 
                WHEN Cliente LIKE '%[_]%' THEN 
                    SUBSTRING(Cliente, CHARINDEX('_', Cliente) + 1, LEN(Cliente))
                ELSE Cliente
            END AS ClienteNormalizado,
            Cliente AS ClienteOriginal,
            Inicio,
            Campaña,
            idInteraccion,
            Atendidas
        FROM detalle_de_interacciones_por_campana_lote
        WHERE Empresa = 'Aurora Salud'
            AND fecha_inicio = @fecha
			AND Duración < 3600
    )
    SELECT 
        MIN(Inicio) AS [Primer contacto],
        MIN(ClienteOriginal) AS [Cliente Original WPP],
        ClienteNormalizado AS [Número de Cliente],
        MIN(Campaña) AS [Campaña Inicial],
        MIN(idInteraccion) AS IdInteraccion
    FROM ClientesNormalizados c1
    WHERE Campaña = 'PilotoWPP_AuroraSalud'
        -- Verificamos que el cliente normalizado no aparezca en las otras campañas
        AND NOT EXISTS (
            SELECT 1
            FROM ClientesNormalizados c2
            WHERE c2.ClienteNormalizado = c1.ClienteNormalizado
                AND c2.Campaña IN ('WhatsappIN', 'MaterImagenesIN', 'MaterTurnosIN')
        )
    GROUP BY ClienteNormalizado
    ORDER BY [Primer contacto];
END;
GO
