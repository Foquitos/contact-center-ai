-- Table [dbo].[Voltara Enerval transferencias internas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval transferencias internas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval transferencias internas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Inicio de transferencia] [datetime] NOT NULL,
	[Fin de transferencia] [datetime] NULL,
	[CONNID] [varchar](50) NULL,
	[Intención de origen] [varchar](100) NULL,
	[Agente de origen] [varchar](50) NULL,
	[Intención de Destino] [varchar](100) NULL,
	[BPO de Destino] [varchar](20) NULL,
	[Agente de Destino] [varchar](50) NULL,
	[Duración del Ring] [int] NULL,
	[Tiempo Hablado] [int] NULL,
	[Duración del Hold] [int] NULL,
	[Última Transferencia] [tinyint] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Voltara_Enerval_transferencias_internas_inicio]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval transferencias internas]') AND name = N'IX_Voltara_Enerval_transferencias_internas_inicio')
CREATE NONCLUSTERED INDEX [IX_Voltara_Enerval_transferencias_internas_inicio] ON [dbo].[Voltara Enerval transferencias internas]
(
	[Inicio de transferencia] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
