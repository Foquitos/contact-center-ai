-- Table [planificacion].[Skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Skill](
	[CampanaID] [int] NOT NULL,
	[SkillID] [int] NOT NULL,
	[Nombre] [nvarchar](80) NOT NULL,
	[PoolID] [int] NULL,
	[ObjetivoNds] [decimal](4, 3) NULL,
	[UmbralSeg] [smallint] NULL,
	[MaxAbandono] [decimal](5, 4) NULL,
	[PacienciaSeg] [int] NULL,
	[Activo] [bit] NOT NULL,
	[Nota] [nvarchar](400) NULL,
	[MaxAsaSeg] [smallint] NULL,
	[ObjetivoNds2] [decimal](4, 3) NULL,
	[UmbralSeg2] [smallint] NULL,
	[MinNivelAtencionB] [decimal](5, 4) NULL,
	[Prioridad] [bit] NOT NULL,
 CONSTRAINT [PK_Plan_Skill] PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC,
	[SkillID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Skill_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Skill] ADD  CONSTRAINT [DF_Plan_Skill_Activo]  DEFAULT ((1)) FOR [Activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Skill_Prioridad]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Skill] ADD  CONSTRAINT [DF_Plan_Skill_Prioridad]  DEFAULT ((0)) FOR [Prioridad]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Skill_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Skill_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Skill_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [FK_Plan_Skill_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Skill_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Skill_Pool] FOREIGN KEY([PoolID])
REFERENCES [planificacion].[Pool] ([PoolID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Skill_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [FK_Plan_Skill_Pool]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Abandono]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Skill_Abandono] CHECK  (([MaxAbandono] IS NULL OR [MaxAbandono]>=(0) AND [MaxAbandono]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Abandono]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [CK_Plan_Skill_Abandono]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Nds]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Skill_Nds] CHECK  (([ObjetivoNds] IS NULL OR [ObjetivoNds]>(0) AND [ObjetivoNds]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Nds]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [CK_Plan_Skill_Nds]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Paciencia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Skill_Paciencia] CHECK  (([PacienciaSeg] IS NULL OR [PacienciaSeg]>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Paciencia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [CK_Plan_Skill_Paciencia]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Planilla]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Skill_Planilla] CHECK  ((([MaxAsaSeg] IS NULL OR [MaxAsaSeg]>(0)) AND ([ObjetivoNds2] IS NULL OR [ObjetivoNds2]>(0) AND [ObjetivoNds2]<=(1)) AND ([UmbralSeg2] IS NULL OR [UmbralSeg2]>(0)) AND ([MinNivelAtencionB] IS NULL OR [MinNivelAtencionB]>(0) AND [MinNivelAtencionB]<=(1)) AND ([ObjetivoNds2] IS NULL AND [UmbralSeg2] IS NULL OR [ObjetivoNds2] IS NOT NULL AND [UmbralSeg2] IS NOT NULL)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Planilla]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [CK_Plan_Skill_Planilla]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Umbral]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Skill_Umbral] CHECK  (([UmbralSeg] IS NULL OR [UmbralSeg]>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Skill_Umbral]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Skill]'))
ALTER TABLE [planificacion].[Skill] CHECK CONSTRAINT [CK_Plan_Skill_Umbral]
GO
