-- Table [orion].[Grabaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Grabaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Grabaciones](
	[ID] [int] NULL,
	[Fecha] [date] NULL,
	[Hora] [time](7) NULL,
	[Duracion] [int] NULL,
	[Nro_Remoto] [varchar](32) NULL,
	[Es_Entrante] [bit] NULL,
	[Agente] [int] NULL,
	[Grupo] [int] NULL,
	[Canal] [int] NULL,
	[Cliente] [int] NULL,
	[Campana] [int] NULL
) ON [PRIMARY]
END
GO
