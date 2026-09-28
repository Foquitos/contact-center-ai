-- Table [dbo].[Ysocial_Consolidado_de_agentes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Ysocial_Consolidado_de_agentes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Ysocial_Consolidado_de_agentes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Cola] [varchar](max) NULL,
	[Asignados a agente] [smallint] NULL,
	[Agrupados] [smallint] NULL,
	[Respondidos] [smallint] NULL,
	[Respuestas verificadas] [smallint] NULL,
	[Descartados] [smallint] NULL,
	[Retornados] [smallint] NULL,
	[Transferidos] [smallint] NULL,
	[Salientes] [smallint] NULL,
	[Mis Casos] [smallint] NULL,
	[Chats finalizados] [smallint] NULL,
	[Casos cerrados] [smallint] NULL,
	[Casos reabiertos] [smallint] NULL,
	[Casos reabiertos por otros] [smallint] NULL,
	[TMO] [time](7) NULL,
	[TMNL] [time](7) NULL,
	[TML] [time](7) NULL,
	[Tiempo de agente] [time](7) NULL,
	[TxH] [float] NULL,
	[RxH] [float] NULL,
	[TMOS] [float] NULL,
	[Total Login] [time](7) NULL,
	[Total Avail] [time](7) NULL,
	[Total Working] [time](7) NULL,
	[Total Aux] [time](7) NULL,
	[Login] [time](7) NULL,
	[Break] [time](7) NULL,
	[Baño] [time](7) NULL,
	[Capacitación] [time](7) NULL,
	[Reunión] [time](7) NULL,
	[Prelogout] [time](7) NULL,
	[BackOffice] [time](7) NULL,
	[Usuario]  AS (substring([Agente],charindex('(',[Agente])+(1),(charindex(')',[Agente])-charindex('(',[Agente]))-(1))) PERSISTED,
	[Casos gestionados] [int] NULL,
 CONSTRAINT [PK_Ysocial_Consolidado_de_agentes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
