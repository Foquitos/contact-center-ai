-- StoredProcedure [orion].[USP_Cargar_Silver_Llamadas_Trafico_Colas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[USP_Cargar_Silver_Llamadas_Trafico_Colas]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [orion].[USP_Cargar_Silver_Llamadas_Trafico_Colas] AS' 
END
GO

ALTER PROCEDURE [orion].[USP_Cargar_Silver_Llamadas_Trafico_Colas]
    @FechaInicio DATE, 
    @FechaFin DATE
AS
BEGIN
    SET NOCOUNT ON;

    -- 1. LIMPIEZA (Garantiza idempotencia)
    DELETE FROM [Acme].[orion].[Silver_Llamadas_Trafico_Colas]
    WHERE Fecha >= @FechaInicio AND Fecha < @FechaFin;

    -- 2. INSERCIÓN DIRECTA DESDE LA TABLA DE DETALLE
    INSERT INTO [Acme].[orion].[Silver_Llamadas_Trafico_Colas] (
        [Fecha], [Campaña], 
        [Inbound_Entrantes], [Inbound_Atendidas], [Inbound_Abandonos], [Inbound_Derivadas], 
        [Inbound_SLA_Menor_60s], [Inbound_Espera_Mayor_240s], [Inbound_Seg_Espera], [Inbound_Seg_Ringing], 
        [Inbound_Seg_Hablado], [Inbound_Seg_Hold], 
        [Outbound_Intentos], [Outbound_Atendidas], [Outbound_No_Atendidas], [Outbound_Seg_Hablado], 
        [Outbound_Seg_Hold], 
        [Total_Gestiones_Efectivas], [Total_Seg_Hablado]
    )
    SELECT 
        CAST(Fecha_Hora AS DATE) AS Fecha,
        Campaña,
        
        -- INBOUND (Métricas Entrantes)
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN 1 ELSE 0 END) AS Inbound_Entrantes,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' AND Resultado_Llamada = 'Atendida' THEN 1 ELSE 0 END) AS Inbound_Atendidas,
        -- NOTA: Agrupamos 'Abandonada' y 'Ag No Atendida' como Abandonos operativos
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' AND Resultado_Llamada IN ('Abandonada', 'Ag No Atendida') THEN 1 ELSE 0 END) AS Inbound_Abandonos,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN Fue_Derivada ELSE 0 END) AS Inbound_Derivadas,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN Cumple_SLA ELSE 0 END) AS Inbound_SLA_Menor_60s,
        -- Solo contamos las que se atendieron pero rompieron el umbral de 240s
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' AND Resultado_Llamada = 'Atendida' AND Segundos_En_Cola > 240 THEN 1 ELSE 0 END) AS Inbound_Espera_Mayor_240s,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN Segundos_En_Cola ELSE 0 END) AS Inbound_Seg_Espera,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN Segundos_Ring ELSE 0 END) AS Inbound_Seg_Ringing,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN Segundos_Hablado ELSE 0 END) AS Inbound_Seg_Hablado,
        SUM(CASE WHEN Tipo_Trafico = 'Inbound' THEN Segundos_Hold ELSE 0 END) AS Inbound_Seg_Hold,

        -- OUTBOUND (Métricas Salientes)
        SUM(CASE WHEN Tipo_Trafico = 'Outbound' THEN 1 ELSE 0 END) AS Outbound_Intentos,
        SUM(CASE WHEN Tipo_Trafico = 'Outbound' AND Resultado_Llamada = 'Atendida' THEN 1 ELSE 0 END) AS Outbound_Atendidas,
        SUM(CASE WHEN Tipo_Trafico = 'Outbound' AND Resultado_Llamada = 'No Atendida' THEN 1 ELSE 0 END) AS Outbound_No_Atendidas,
        SUM(CASE WHEN Tipo_Trafico = 'Outbound' THEN Segundos_Hablado ELSE 0 END) AS Outbound_Seg_Hablado,
        SUM(CASE WHEN Tipo_Trafico = 'Outbound' THEN Segundos_Hold ELSE 0 END) AS Outbound_Seg_Hold,

        -- TOTALES CONSOLIDADOS
        SUM(CASE WHEN Resultado_Llamada = 'Atendida' THEN 1 ELSE 0 END) AS Total_Gestiones_Efectivas,
        SUM(Segundos_Hablado) AS Total_Seg_Hablado

    FROM [Acme].[orion].[Silver_Llamadas_Detalle]
    WHERE CAST(Fecha_Hora AS DATE) >= @FechaInicio AND CAST(Fecha_Hora AS DATE) < @FechaFin
    GROUP BY 
        CAST(Fecha_Hora AS DATE),
        Campaña;
END;
GO
