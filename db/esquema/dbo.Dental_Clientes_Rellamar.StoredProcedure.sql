-- StoredProcedure [dbo].[Dental_Clientes_Rellamar]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Dental_Clientes_Rellamar]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[Dental_Clientes_Rellamar] AS' 
END
GO
ALTER PROCEDURE [dbo].[Dental_Clientes_Rellamar]
    @fecha DATE
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        MIN(Inicio) AS [Primer llamado],
        CASE min(Campaña)
		WHEN 'WhatsappIN' THEN 'Whatsapp'
		ELSE 'Llamada' END AS [Tipo Contacto],
        Cliente,
        MIN(Campaña) AS Campaña,
        MIN(idInteraccion) AS IdInteraccion
    FROM detalle_de_interacciones_por_campana_lote
    WHERE Empresa = 'Odonto Plus'
        AND fecha_inicio = @fecha
        AND Campaña IN ('dentalPrioridad', 'dentalNoPrioridad', 'WhatsappIN')
        AND Entrante = 1
        AND Atendidas = 0
        AND Cliente NOT IN 
            (SELECT
                Cliente
            FROM detalle_de_interacciones_por_campana_lote
            WHERE Empresa = 'Odonto Plus'
                AND fecha_inicio = @fecha
                AND Campaña IN ('dentalPrioridad', 'dentalNoPrioridad', 'WhatsappIN')
                AND Entrante = 1
                AND Atendidas = 1
                AND Cliente IN
                    (SELECT
                        Cliente
                    FROM detalle_de_interacciones_por_campana_lote
                    WHERE Empresa = 'Odonto Plus'
                        AND fecha_inicio = @fecha
                        AND Campaña IN ('dentalPrioridad', 'dentalNoPrioridad', 'WhatsappIN')
                        AND Entrante = 1
                        AND Atendidas = 0)
            )
    GROUP BY Cliente;
END;
GO
