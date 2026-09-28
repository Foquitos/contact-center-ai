-- StoredProcedure [dbo].[InsertarAcumuladoresPorSkill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[InsertarAcumuladoresPorSkill]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[InsertarAcumuladoresPorSkill] AS' 
END
GO
ALTER PROCEDURE [dbo].[InsertarAcumuladoresPorSkill]
    @Agente VARCHAR(MAX),
    @campana_ejemplo VARCHAR(MAX),  -- Nombre de la campaña existente (filtro)
    @campana_nueva VARCHAR(MAX),    -- Nombre de la campaña nueva (para insertar)
    @desde DATE,
    @hasta DATE
AS
BEGIN
    -- Insertar datos en la tabla acumuladores_de_agentes_por_skill
    INSERT INTO acumuladores_de_agentes_por_skill 
    SELECT 
        [LOGIN ID],
        [AGENTE],
        @campana_nueva AS [CAMPAÑA],  -- Usamos el parámetro @campana_nueva
        [INTERVALO],
        [LOGIN (s)],
        [AVAIL (s)],
        [PREVIEW (s)],
        [DIAL (s)],
        [RING (s)],
        [CONNECT (s)],
        [HOLD (s)],
        [ACW (s)],
        [NOT READY (s)],
        [OTHER (s)],
        [BREAK (s)],
        [BAÑO (s)],
        [ENTRENAMIENTO (s)],
        [CAPACITACIÓN (s)],
        [ADMINISTRATIVO (s)],
        [SUPERVISIÓN (s)],
        [LLAMADO.SALIENTE (s)],
        [GESTIÓN.FUERA.LÍNEA (s)],
        [NO.DISPONIBLE (s)],
        [TUTORES (s)],
        [AUXILIAR TOTAL (s)],
        [AUXILIAR %],
        [TIEMPO REAL DE LOGUEO],
        [UTILIZACIÓN],
        [AHT (s)],
        [ATT (s)],
        [ATENDIDAS],
        [NO ATENDIDAS],
        [TRANSFER IN],
        [TRANSFER OUT],
        [TIPIFICACIÓN EXITOSO],
        [TIPIFICACIÓN NO EXITOSO],
        [TIPIFICACIÓN NO EFECTIVO],
        [TIPIFICACIÓN NEUTRO],
        [TIPIFICACIÓN OTRO],
        [CE],
        [%CE],
        [EXITOS / CE],
        [EXITOS POR HORA REAL]
    FROM 
        [Acme].[dbo].[acumuladores_de_agentes_por_skill]
    WHERE 
        AGENTE = @Agente
        AND CAMPAÑA = @campana_ejemplo  -- Filtramos por la campaña existente
        AND (CAST(INTERVALO AS DATE) BETWEEN @desde AND @hasta);  -- Filtramos por rango de fechas
END;
GO
