-- Table [dbo].[Gasur Llamadas despacho]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Llamadas despacho]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Llamadas despacho](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [datetime] NULL,
	[Cola] [varchar](max) NULL,
	[Destino] [varchar](max) NULL,
	[Espera (seg.)] [smallint] NULL,
	[Duración (seg.)] [smallint] NULL,
	[Es Entrante] [bit] NULL,
	[Completa Agente] [bit] NULL,
	[Espera] [time](7) NULL,
	[Duracion] [time](7) NULL,
	[Agente] [varchar](max) NULL,
	[Localidad] [varchar](max) NULL,
	[Intentos] [tinyint] NULL,
 CONSTRAINT [PK_Gasur Llamadas despacho] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
