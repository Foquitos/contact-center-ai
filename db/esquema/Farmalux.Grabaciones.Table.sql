-- Table [Farmalux].[Grabaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Farmalux].[Grabaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [Farmalux].[Grabaciones](
	[Fecha/Hora] [datetime] NULL,
	[Origen] [varchar](128) NULL,
	[Destino] [varchar](128) NULL,
	[Duración] [int] NULL,
	[Estado] [varchar](128) NULL,
	[Entidad] [varchar](128) NULL,
	[Usuario] [varchar](128) NULL,
	[Tipificación 1] [varchar](128) NULL,
	[Tipificación 2] [varchar](128) NULL,
	[Cola Entrante] [varchar](128) NULL,
	[Agente que Atendió] [varchar](128) NULL,
	[DataFijos] [varchar](128) NULL,
	[DataPers] [varchar](128) NULL
) ON [PRIMARY]
END
GO
