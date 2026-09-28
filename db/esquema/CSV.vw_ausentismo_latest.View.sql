-- View [CSV].[vw_ausentismo_latest]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[CSV].[vw_ausentismo_latest]'))
EXEC dbo.sp_executesql @statement = N'CREATE   VIEW [CSV].[vw_ausentismo_latest] AS
       SELECT *
       FROM CSV.ausentismo_rt
       WHERE batch_id = (SELECT MAX(batch_id) FROM CSV.ausentismo_rt)' 
GO
