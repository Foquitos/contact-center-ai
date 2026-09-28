-- Table [Farmalux].[SalesForce_casos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Farmalux].[SalesForce_casos]') AND type in (N'U'))
BEGIN
CREATE TABLE [Farmalux].[SalesForce_casos](
	[Propietario del caso] [varchar](100) NULL,
	[Nombre de la cuenta] [varchar](100) NULL,
	[Asunto] [varchar](255) NULL,
	[Fecha/Hora de apertura] [datetime] NULL,
	[Antigüedad] [bigint] NULL,
	[Abierto] [bit] NULL,
	[Cerrado] [bit] NULL,
	[Motivo del caso] [varchar](50) NULL,
	[Tipo] [varchar](50) NULL,
	[Comentarios del caso] [varchar](1024) NULL,
	[Nombres] [varchar](100) NULL,
	[Contacto: Teléfono] [varchar](100) NULL,
	[Última fecha/hora de modificación de caso] [datetime] NULL,
	[Número del caso] [int] NULL,
	[Voz del cliente] [varchar](255) NULL,
	[Motivo] [varchar](255) NULL,
	[Submotivo] [varchar](255) NULL
) ON [PRIMARY]
END
GO
