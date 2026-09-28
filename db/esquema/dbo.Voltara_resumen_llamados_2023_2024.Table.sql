-- Table [dbo].[Voltara_resumen_llamados_2023_2024]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_resumen_llamados_2023_2024]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_resumen_llamados_2023_2024](
	[Intervalo] [datetime] NULL,
	[TotalLlamadasEntrantes] [smallint] NULL
) ON [PRIMARY]
END
GO
