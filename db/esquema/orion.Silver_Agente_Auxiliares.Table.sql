-- Table [orion].[Silver_Agente_Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Silver_Agente_Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Silver_Agente_Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NOT NULL,
	[Legajo_Orion] [varchar](50) NOT NULL,
	[Campaña] [varchar](100) NOT NULL,
	[Total_Login_Segundos] [int] NULL,
	[Total_Auxiliares_Segundos] [int] NULL,
	[Segundos_Ready] [int] NULL,
	[Segundos_NotReady] [int] NULL,
	[Conteo_Pausas] [int] NULL,
	[Baño] [int] NULL,
	[Descanso Agente] [int] NULL,
	[Cap. Campaña] [int] NULL,
	[Cap. Eventual] [int] NULL,
	[Back Office] [int] NULL,
	[Cons. Supervisor] [int] NULL,
	[Almuerzo] [int] NULL,
	[libre por no atención] [int] NULL,
	[TotalPausa9] [int] NULL,
	[TotalPausa10] [int] NULL,
	[TotalPausa11] [int] NULL,
	[TotalPausa12] [int] NULL,
	[TotalPausa13] [int] NULL,
	[TotalPausa14] [int] NULL,
	[TotalPausa15] [int] NULL,
	[TotalPausa16] [int] NULL,
 CONSTRAINT [PK_Silver_Agente_Auxiliares] PRIMARY KEY NONCLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [CIX_Silver_Agente_Login_Aux_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[orion].[Silver_Agente_Auxiliares]') AND name = N'CIX_Silver_Agente_Login_Aux_Fecha')
CREATE CLUSTERED INDEX [CIX_Silver_Agente_Login_Aux_Fecha] ON [orion].[Silver_Agente_Auxiliares]
(
	[Fecha] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [NCIX_Silver_Agente_Login_Aux_Agente_Campana]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[orion].[Silver_Agente_Auxiliares]') AND name = N'NCIX_Silver_Agente_Login_Aux_Agente_Campana')
CREATE NONCLUSTERED INDEX [NCIX_Silver_Agente_Login_Aux_Agente_Campana] ON [orion].[Silver_Agente_Auxiliares]
(
	[Legajo_Orion] ASC,
	[Campaña] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
