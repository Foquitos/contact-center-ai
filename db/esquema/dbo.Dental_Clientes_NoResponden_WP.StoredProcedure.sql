-- StoredProcedure [dbo].[Dental_Clientes_NoResponden_WP]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Dental_Clientes_NoResponden_WP]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[Dental_Clientes_NoResponden_WP] AS' 
END
GO
ALTER PROCEDURE [dbo].[Dental_Clientes_NoResponden_WP]
    @fecha_desde DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @fecha_desde IS NULL
        SET @fecha_desde = CAST(DATEADD(DAY, -2, GETDATE()) AS DATE);

--Base de interacciones normalizada
    WITH BaseClientes AS (
    SELECT
        Campaña,
        Inicio,
        idInteraccion,
        Tipificación,
        [Tipo Contacto],
        Cliente AS ClienteOriginal,
        fecha_inicio,
        Entrante,
        Atendidas,
        -- Normalizar el 'Cliente' según la campaña
        CASE
            WHEN Campaña IN ('dentalPrioridad', 'dentalNoPrioridad') THEN SUBSTRING(Cliente, 1, 10)
            WHEN Campaña IN ('WhatsappIN', 'Transferencias_inbound_WhatsApp') THEN SUBSTRING(RIGHT(Cliente, LEN(Cliente) - CHARINDEX('_', Cliente)), 4, 10)
            WHEN Campaña IN ('Formulario Recontacto WhatsApp', 'Salientes RRSS', 'Salientes Telefono') THEN SUBSTRING(Cliente, 2, 10)
            ELSE Cliente
        END AS ClienteNormalizado
    FROM [dbo].[detalle_de_interacciones_por_campana_lote]
    WHERE
        fecha_inicio >= @fecha_desde
        AND Empresa = 'Odonto Plus'
        AND Campaña IN ('dentalPrioridad','dentalNoPrioridad','WhatsappIN','Transferencias_inbound_WhatsApp','Formulario Recontacto WhatsApp','Salientes RRSS','In-Out Formulario WhatsApp','Salientes Telefono')
),

-- Identificar la primera interacción de "No responde" por Whatsapp
PrimeraInteraccionNoRespondidaWhatsapp AS (
    SELECT
        ClienteNormalizado,
        Campaña AS Campaña_Primer_Intento,
        Inicio AS Inicio_Primer_Intento,
        idInteraccion AS idInteraccion_Primer_Intento,
        Tipificación AS Tipificacion_Primer_Intento,
        [Tipo Contacto] AS Tipo_Contacto_Primer_Intento
    FROM (
        SELECT
            ClienteNormalizado,
            Campaña,
            Inicio,
            idInteraccion,
            Tipificación,
            [Tipo Contacto],
            ROW_NUMBER() OVER (PARTITION BY ClienteNormalizado ORDER BY Inicio) as rn
        FROM BaseClientes
        WHERE
            Campaña = 'WhatsappIN'
            AND Tipificación LIKE '%No resp%'
    ) AS RankedWhatsappInteractions
    WHERE rn = 1
),

-- CTE para identificar las interacciones exitosas posteriores
-- No necesitamos todos los detalles, solo si EXISTIÓ una.
ClientesConRecontactoExitoso AS (
    SELECT DISTINCT
        BC.ClienteNormalizado
    FROM BaseClientes AS BC
    INNER JOIN PrimeraInteraccionNoRespondidaWhatsapp AS PINRW
        ON BC.ClienteNormalizado = PINRW.ClienteNormalizado
    WHERE
        BC.Inicio > PINRW.Inicio_Primer_Intento
        AND BC.Atendidas = 1 -- Interacción exitosa
        AND BC.Campaña IN ('dentalPrioridad', 'dentalNoPrioridad', 'WhatsappIN', 'Transferencias_inbound_WhatsApp', 'Formulario Recontacto WhatsApp', 'Salientes RRSS', 'In-Out Formulario WhatsApp', 'Salientes Telefono')
)

-- Selección final: Primera interacción fallida de WhatsApp que NO tuvo un recontacto exitoso
SELECT
    PINRW.ClienteNormalizado AS Cliente,
    PINRW.Campaña_Primer_Intento AS Campaña,
    PINRW.Inicio_Primer_Intento AS [Primer Contacto],
    PINRW.idInteraccion_Primer_Intento AS idInteraccion,
    PINRW.Tipificacion_Primer_Intento AS Tipificacion
FROM PrimeraInteraccionNoRespondidaWhatsapp AS PINRW
LEFT JOIN ClientesConRecontactoExitoso AS CCRE
    ON PINRW.ClienteNormalizado = CCRE.ClienteNormalizado
WHERE
    CCRE.ClienteNormalizado IS NULL -- Condición para la exclusión
ORDER BY PINRW.Inicio_Primer_Intento;
END
GO
