-- Table [dbo].[Pronostico_Llamadas_Voltara]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Pronostico_Llamadas_Voltara]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Pronostico_Llamadas_Voltara](
	[fecha] [date] NULL,
	[hora] [time](7) NULL,
	[pronostico] [smallint] NULL
) ON [PRIMARY]
END
GO
