-- Table [planificacion].[Corrida]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Corrida]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Corrida](
	[CorridaID] [bigint] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[Horizonte] [nvarchar](20) NOT NULL,
	[Desde] [date] NOT NULL,
	[Hasta] [date] NOT NULL,
	[Modelo] [nvarchar](60) NULL,
	[Estado] [nvarchar](20) NOT NULL,
	[Metricas] [nvarchar](max) NULL,
	[Motivo] [nvarchar](400) NULL,
	[EsVigente] [bit] NOT NULL,
	[CreadoPor] [int] NULL,
	[CreadoEn] [datetime2](0) NOT NULL,
	[TerminadoEn] [datetime2](0) NULL,
 CONSTRAINT [PK_Plan_Corrida] PRIMARY KEY CLUSTERED 
(
	[CorridaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [UX_Plan_Corrida_vigente]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[planificacion].[Corrida]') AND name = N'UX_Plan_Corrida_vigente')
CREATE UNIQUE NONCLUSTERED INDEX [UX_Plan_Corrida_vigente] ON [planificacion].[Corrida]
(
	[CampanaID] ASC,
	[Horizonte] ASC
)
WHERE ([EsVigente]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Corrida_Estado]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Corrida] ADD  CONSTRAINT [DF_Plan_Corrida_Estado]  DEFAULT ('EN_CURSO') FOR [Estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Corrida_Vigente]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Corrida] ADD  CONSTRAINT [DF_Plan_Corrida_Vigente]  DEFAULT ((0)) FOR [EsVigente]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Corrida_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Corrida] ADD  CONSTRAINT [DF_Plan_Corrida_Fecha]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Corrida_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Corrida_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Corrida_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida] CHECK CONSTRAINT [FK_Plan_Corrida_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Corrida_Estado]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Corrida_Estado] CHECK  (([Estado]='ERROR' OR [Estado]='OK' OR [Estado]='EN_CURSO'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Corrida_Estado]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida] CHECK CONSTRAINT [CK_Plan_Corrida_Estado]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Corrida_Horizonte]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Corrida_Horizonte] CHECK  (([Horizonte]='presupuesto' OR [Horizonte]='operativo'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Corrida_Horizonte]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida] CHECK CONSTRAINT [CK_Plan_Corrida_Horizonte]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Corrida_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Corrida_Rango] CHECK  (([Hasta]>=[Desde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Corrida_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Corrida]'))
ALTER TABLE [planificacion].[Corrida] CHECK CONSTRAINT [CK_Plan_Corrida_Rango]
GO
