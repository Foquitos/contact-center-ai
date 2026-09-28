-- Table [dbo].[Gasur Extendidos despacho]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Extendidos despacho]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Extendidos despacho](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Logueado] [int] NULL,
	[Disponible] [int] NULL,
	[Hablado] [int] NULL,
	[Hablado entrante] [int] NULL,
	[Hablado saliente] [int] NULL,
	[Promedio de Respuesta] [int] NULL,
	[Puesto en espera entrante] [int] NULL,
	[Puesto en espera saliente] [int] NULL,
	[Puesto en espera (total)] [int] NULL,
	[Pausado] [int] NULL,
	[Promedio Entrante] [int] NULL,
	[Promedio Entrante y Saliente] [int] NULL,
	[Presentadas] [tinyint] NULL,
	[Atendidas] [tinyint] NULL,
	[Finalizadas por agente] [tinyint] NULL,
	[Finalizadas por cliente] [tinyint] NULL,
	[Perdidas] [tinyint] NULL,
	[Transferidas] [tinyint] NULL,
	[Salientes] [tinyint] NULL,
	[Salientes locales] [tinyint] NULL,
	[Salientes externas] [tinyint] NULL,
	[Otras salientes] [tinyint] NULL,
	[Perdidas por cliente] [tinyint] NULL,
	[Perdidas por agente] [tinyint] NULL,
 CONSTRAINT [PK_Gasur Extendidos despacho] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
