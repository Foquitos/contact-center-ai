-- Table [orion].[Acumulado_por_Skills]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Acumulado_por_Skills]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Acumulado_por_Skills](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Skill_ID] [int] NULL,
	[Skill_Nombre] [varchar](100) NULL,
	[Cant_Entrantes] [int] NULL,
	[Cant_Atendidas] [int] NULL,
	[Cant_Abandonadas] [int] NULL,
	[Atendidas_Bajo_SLA] [int] NULL,
	[Total_Seg_Hablado] [bigint] NULL,
	[Total_Seg_Hold] [bigint] NULL,
	[Total_Seg_Espera] [bigint] NULL,
	[Total_Seg_Ringing] [bigint] NULL,
	[Total_Seg_Duracion] [bigint] NULL,
	[Atendidas_Mayor_240s] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Fecha_Skill]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[orion].[Acumulado_por_Skills]') AND name = N'IX_Fecha_Skill')
CREATE NONCLUSTERED INDEX [IX_Fecha_Skill] ON [orion].[Acumulado_por_Skills]
(
	[Fecha] ASC,
	[Skill_ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
