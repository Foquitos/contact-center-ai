-- Table [dbo].[Gasur Llamadas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Llamadas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Llamadas](
	[Id] [float] NULL,
	[Fecha] [datetime] NULL,
	[Cola] [varchar](max) NULL,
	[Orígen] [varchar](50) NULL,
	[Destino] [varchar](max) NULL,
	[Espera (seg.)] [smallint] NULL,
	[Duración (seg.)] [smallint] NULL,
	[Tipo] [varchar](max) NULL,
	[Detección Máquina] [varchar](max) NULL,
	[Estado] [varchar](max) NULL,
	[Etiquetas] [float] NULL,
	[Agente] [varchar](max) NULL,
	[Localidad] [varchar](max) NULL,
	[Intentos] [tinyint] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
