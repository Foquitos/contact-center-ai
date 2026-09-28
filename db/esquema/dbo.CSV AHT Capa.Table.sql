-- Table [dbo].[CSV AHT Capa]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV AHT Capa]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV AHT Capa](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Identif. de conexion] [int] NULL,
	[Split/Skill] [smallint] NULL,
	[Llamadas ACD] [smallint] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo ACW] [smallint] NULL,
	[Tiempo ACD] [int] NULL,
	[Tiempo de reten.] [int] NULL,
	[RINGTIME] [smallint] NULL,
	[AHT] [int] NULL,
	[PCRC ID] [smallint] NULL,
	[SITE] [varchar](max) NULL,
	[Fecha Ingreso] [date] NULL,
	[Fecha ultima capacitacion] [date] NULL,
	[Operador] [varchar](max) NULL,
	[Supervisor] [varchar](max) NULL,
	[Legajo] [int] NULL,
 CONSTRAINT [PK_CSV AHT Capa] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
