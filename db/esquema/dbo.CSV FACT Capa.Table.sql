-- Table [dbo].[CSV FACT Capa]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV FACT Capa]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV FACT Capa](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Identif. de conexion] [int] NULL,
	[Split/Skill] [smallint] NULL,
	[Llamadas ACD] [smallint] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo ACW] [smallint] NULL,
	[Tiempo ACD] [int] NULL,
	[Tiempo de reten.] [smallint] NULL,
	[RINGTIME] [smallint] NULL,
	[AH] [int] NULL,
	[PCRC ID] [int] NULL,
	[SITE] [varchar](max) NULL,
	[Fecha Ingreso] [date] NULL,
	[Es Upgrade] [bit] NULL,
	[Fecha ultima capacitacion] [date] NULL,
	[Expertise ID] [int] NULL,
	[AHT] [float] NULL,
	[STD] [float] NULL,
	[-] [float] NULL,
	[STD FINAL] [float] NULL,
	[ESTADO] [varchar](max) NULL,
	[OPERADOR] [varchar](max) NULL,
	[SUPERVISOR] [varchar](max) NULL,
	[PCRC CAPACITACION] [varchar](max) NULL,
	[SEMANA] [tinyint] NULL,
 CONSTRAINT [PK_CSV FACT Capa] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
