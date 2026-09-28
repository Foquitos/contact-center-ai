-- Table [planificacion].[Asignacion]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Asignacion]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Asignacion](
	[AsignacionID] [int] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[SkillID] [int] NULL,
	[VigenteDesde] [date] NOT NULL,
	[VigenteHasta] [date] NULL,
	[Porcentaje] [decimal](5, 4) NOT NULL,
	[Nota] [nvarchar](400) NULL,
	[CreadoPor] [int] NULL,
	[CreadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_Asignacion] PRIMARY KEY CLUSTERED 
(
	[AsignacionID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Plan_Asignacion_vigencia]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[planificacion].[Asignacion]') AND name = N'IX_Plan_Asignacion_vigencia')
CREATE NONCLUSTERED INDEX [IX_Plan_Asignacion_vigencia] ON [planificacion].[Asignacion]
(
	[CampanaID] ASC,
	[VigenteDesde] ASC
)
INCLUDE([SkillID],[VigenteHasta],[Porcentaje]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Asignacion_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Asignacion] ADD  CONSTRAINT [DF_Plan_Asignacion_Fecha]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Asignacion_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Asignacion]'))
ALTER TABLE [planificacion].[Asignacion]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Asignacion_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Asignacion_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Asignacion]'))
ALTER TABLE [planificacion].[Asignacion] CHECK CONSTRAINT [FK_Plan_Asignacion_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Asignacion_Pct]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Asignacion]'))
ALTER TABLE [planificacion].[Asignacion]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Asignacion_Pct] CHECK  (([Porcentaje]>=(0) AND [Porcentaje]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Asignacion_Pct]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Asignacion]'))
ALTER TABLE [planificacion].[Asignacion] CHECK CONSTRAINT [CK_Plan_Asignacion_Pct]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Asignacion_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Asignacion]'))
ALTER TABLE [planificacion].[Asignacion]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Asignacion_Rango] CHECK  (([VigenteHasta] IS NULL OR [VigenteHasta]>=[VigenteDesde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Asignacion_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Asignacion]'))
ALTER TABLE [planificacion].[Asignacion] CHECK CONSTRAINT [CK_Plan_Asignacion_Rango]
GO
