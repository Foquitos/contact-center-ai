-- StoredProcedure [dbo].[sp_LimpiarDatosDeBatchFinalizados]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[sp_LimpiarDatosDeBatchFinalizados]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [dbo].[sp_LimpiarDatosDeBatchFinalizados] AS' 
END
GO

-- 3) La limpieza pasa a respetar procesado_at --------------------------------------------
ALTER   PROCEDURE [dbo].[sp_LimpiarDatosDeBatchFinalizados]
AS
BEGIN
    SET NOCOUNT ON;

    -- Se borra la metadata SOLO de lotes con los que ya terminamos:
    --   * fallidos/cancelados/expirados en Gemini -> no hay resultados que guardar;
    --   * exitosos YA PROCESADOS (procesado_at sellado por Auditor._sellar_batch_procesado).
    -- Un lote exitoso todavía sin procesar conserva su Batch_data: es lo que permite que
    -- check_batch_status lo reintente en la próxima pasada en vez de perderlo.
    DELETE bd
    FROM calidad.[Batch_data] AS bd
    JOIN calidad.BatchJobs AS bj
      ON bj.[name] = bd.Batch_id
    WHERE bj.status IN ('JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED')
       OR (bj.status = 'JOB_STATE_SUCCEEDED' AND bj.procesado_at IS NOT NULL);
END
GO
