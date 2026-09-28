-- Table [planificacion].[CurvaAntiguedad]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[CurvaAntiguedad](
	[CampanaID] [int] NOT NULL,
	[DiaDesde] [smallint] NOT NULL,
	[DiaHasta] [smallint] NULL,
	[Rinde] [decimal](5, 4) NOT NULL,
	[Factor] [decimal](5, 4) NOT NULL,
	[Llamadas] [int] NOT NULL,
	[Participacion] [decimal](5, 4) NULL,
	[MedidoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_CurvaAntiguedad] PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC,
	[DiaDesde] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_CurvaAntiguedad_MedidoEn]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[CurvaAntiguedad] ADD  CONSTRAINT [DF_Plan_CurvaAntiguedad_MedidoEn]  DEFAULT (sysdatetime()) FOR [MedidoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Desde]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CurvaAntiguedad_Desde] CHECK  (([DiaDesde]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Desde]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad] CHECK CONSTRAINT [CK_Plan_CurvaAntiguedad_Desde]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CurvaAntiguedad_Factor] CHECK  (([Factor]>(0) AND [Factor]<=(2)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad] CHECK CONSTRAINT [CK_Plan_CurvaAntiguedad_Factor]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Hasta]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CurvaAntiguedad_Hasta] CHECK  (([DiaHasta] IS NULL OR [DiaHasta]>[DiaDesde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Hasta]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad] CHECK CONSTRAINT [CK_Plan_CurvaAntiguedad_Hasta]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Part]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CurvaAntiguedad_Part] CHECK  (([Participacion] IS NULL OR [Participacion]>=(0) AND [Participacion]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Part]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad] CHECK CONSTRAINT [CK_Plan_CurvaAntiguedad_Part]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Rinde]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CurvaAntiguedad_Rinde] CHECK  (([Rinde]>(0) AND [Rinde]<=(2)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CurvaAntiguedad_Rinde]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CurvaAntiguedad]'))
ALTER TABLE [planificacion].[CurvaAntiguedad] CHECK CONSTRAINT [CK_Plan_CurvaAntiguedad_Rinde]
GO
