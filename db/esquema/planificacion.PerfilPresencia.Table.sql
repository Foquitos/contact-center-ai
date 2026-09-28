-- Table [planificacion].[PerfilPresencia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[PerfilPresencia](
	[CampanaID] [int] NOT NULL,
	[TipoDia] [varchar](10) NOT NULL,
	[Minuto] [smallint] NOT NULL,
	[Exceso] [decimal](6, 4) NOT NULL,
	[Faltante] [decimal](7, 4) NULL,
	[Muestras] [int] NOT NULL,
	[MedidoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_PerfilPresencia] PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC,
	[TipoDia] ASC,
	[Minuto] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_PerfilPresencia_MedidoEn]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[PerfilPresencia] ADD  CONSTRAINT [DF_Plan_PerfilPresencia_MedidoEn]  DEFAULT (sysdatetime()) FOR [MedidoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PerfilPresencia_Exceso]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]'))
ALTER TABLE [planificacion].[PerfilPresencia]  WITH CHECK ADD  CONSTRAINT [CK_Plan_PerfilPresencia_Exceso] CHECK  (([Exceso]>=(-0.5) AND [Exceso]<=(0.5)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PerfilPresencia_Exceso]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]'))
ALTER TABLE [planificacion].[PerfilPresencia] CHECK CONSTRAINT [CK_Plan_PerfilPresencia_Exceso]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PerfilPresencia_Minuto]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]'))
ALTER TABLE [planificacion].[PerfilPresencia]  WITH CHECK ADD  CONSTRAINT [CK_Plan_PerfilPresencia_Minuto] CHECK  (([Minuto]>=(0) AND [Minuto]<(1440)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PerfilPresencia_Minuto]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]'))
ALTER TABLE [planificacion].[PerfilPresencia] CHECK CONSTRAINT [CK_Plan_PerfilPresencia_Minuto]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PerfilPresencia_Tipo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]'))
ALTER TABLE [planificacion].[PerfilPresencia]  WITH CHECK ADD  CONSTRAINT [CK_Plan_PerfilPresencia_Tipo] CHECK  (([TipoDia]='no_habil' OR [TipoDia]='habil'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PerfilPresencia_Tipo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PerfilPresencia]'))
ALTER TABLE [planificacion].[PerfilPresencia] CHECK CONSTRAINT [CK_Plan_PerfilPresencia_Tipo]
GO
