-- Table [dbo].[Gasur Extendido]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Extendido]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Extendido](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente] [varchar](max) NULL,
	[Logueado] [time](7) NULL,
	[Disponible] [time](7) NULL,
	[Hablado] [time](7) NULL,
	[Hablado entrante] [time](7) NULL,
	[Hablado saliente] [time](7) NULL,
	[Promedio de Respuesta] [time](7) NULL,
	[Puesto en espera entrante] [time](7) NULL,
	[Puesto en espera saliente] [time](7) NULL,
	[Puesto en espera (total)] [time](7) NULL,
	[Pausado] [time](7) NULL,
	[Promedio Entrante] [time](7) NULL,
	[Promedio Entrante y Saliente] [time](7) NULL,
	[Presentadas] [smallint] NULL,
	[Atendidas] [smallint] NULL,
	[Finalizadas por agente] [smallint] NULL,
	[Finalizadas por cliente] [smallint] NULL,
	[Perdidas] [smallint] NULL,
	[Transferidas] [smallint] NULL,
	[Salientes] [smallint] NULL,
	[Salientes locales] [smallint] NULL,
	[Salientes externas] [smallint] NULL,
	[Otras salientes] [smallint] NULL,
	[Perdidas por cliente] [smallint] NULL,
	[Perdidas por agente] [smallint] NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Gasur Extendido] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
