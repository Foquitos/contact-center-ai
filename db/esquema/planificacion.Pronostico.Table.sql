-- Table [planificacion].[Pronostico]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Pronostico]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Pronostico](
	[CorridaID] [bigint] NOT NULL,
	[SkillID] [int] NOT NULL,
	[Intervalo] [datetime2](0) NOT NULL,
	[LlamadasTotal] [decimal](10, 2) NULL,
	[Asignacion] [decimal](5, 4) NULL,
	[LlamadasAcme] [decimal](10, 2) NOT NULL,
	[LlamadasBase] [decimal](10, 2) NULL,
	[TmoSeg] [decimal](8, 2) NOT NULL,
 CONSTRAINT [PK_Plan_Pronostico] PRIMARY KEY CLUSTERED 
(
	[CorridaID] ASC,
	[SkillID] ASC,
	[Intervalo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Pronostico_Corrida]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pronostico]'))
ALTER TABLE [planificacion].[Pronostico]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Pronostico_Corrida] FOREIGN KEY([CorridaID])
REFERENCES [planificacion].[Corrida] ([CorridaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Pronostico_Corrida]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pronostico]'))
ALTER TABLE [planificacion].[Pronostico] CHECK CONSTRAINT [FK_Plan_Pronostico_Corrida]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Pronostico_Llamadas]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pronostico]'))
ALTER TABLE [planificacion].[Pronostico]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Pronostico_Llamadas] CHECK  (([LlamadasAcme]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Pronostico_Llamadas]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pronostico]'))
ALTER TABLE [planificacion].[Pronostico] CHECK CONSTRAINT [CK_Plan_Pronostico_Llamadas]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Pronostico_Tmo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pronostico]'))
ALTER TABLE [planificacion].[Pronostico]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Pronostico_Tmo] CHECK  (([TmoSeg]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Pronostico_Tmo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pronostico]'))
ALTER TABLE [planificacion].[Pronostico] CHECK CONSTRAINT [CK_Plan_Pronostico_Tmo]
GO
