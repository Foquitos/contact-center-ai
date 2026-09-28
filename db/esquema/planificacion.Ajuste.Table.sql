-- Table [planificacion].[Ajuste]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Ajuste]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Ajuste](
	[AjusteID] [int] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[SkillID] [int] NULL,
	[Desde] [datetime2](0) NOT NULL,
	[Hasta] [datetime2](0) NOT NULL,
	[Factor] [decimal](6, 3) NOT NULL,
	[Motivo] [nvarchar](400) NOT NULL,
	[Activo] [bit] NOT NULL,
	[CreadoPor] [int] NULL,
	[CreadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_Ajuste] PRIMARY KEY CLUSTERED 
(
	[AjusteID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Plan_Ajuste_rango]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[planificacion].[Ajuste]') AND name = N'IX_Plan_Ajuste_rango')
CREATE NONCLUSTERED INDEX [IX_Plan_Ajuste_rango] ON [planificacion].[Ajuste]
(
	[CampanaID] ASC,
	[Desde] ASC,
	[Hasta] ASC
)
WHERE ([Activo]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Ajuste_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Ajuste] ADD  CONSTRAINT [DF_Plan_Ajuste_Activo]  DEFAULT ((1)) FOR [Activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Ajuste_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Ajuste] ADD  CONSTRAINT [DF_Plan_Ajuste_Fecha]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Ajuste_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Ajuste]'))
ALTER TABLE [planificacion].[Ajuste]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Ajuste_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Ajuste_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Ajuste]'))
ALTER TABLE [planificacion].[Ajuste] CHECK CONSTRAINT [FK_Plan_Ajuste_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Ajuste_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Ajuste]'))
ALTER TABLE [planificacion].[Ajuste]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Ajuste_Factor] CHECK  (([Factor]>(0) AND [Factor]<=(10)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Ajuste_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Ajuste]'))
ALTER TABLE [planificacion].[Ajuste] CHECK CONSTRAINT [CK_Plan_Ajuste_Factor]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Ajuste_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Ajuste]'))
ALTER TABLE [planificacion].[Ajuste]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Ajuste_Rango] CHECK  (([Hasta]>[Desde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Ajuste_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Ajuste]'))
ALTER TABLE [planificacion].[Ajuste] CHECK CONSTRAINT [CK_Plan_Ajuste_Rango]
GO
