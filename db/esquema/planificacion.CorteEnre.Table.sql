-- Table [planificacion].[CorteEnre]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[CorteEnre]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[CorteEnre](
	[Momento] [datetime2](0) NOT NULL,
	[Distribuidora] [varchar](10) NOT NULL,
	[Fuente] [varchar](10) NOT NULL,
	[UsuariosSinServicio] [int] NOT NULL,
	[AfectadosMedia] [int] NULL,
	[AfectadosBaja] [int] NULL,
	[Cortes] [int] NULL,
	[CargadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_CorteEnre] PRIMARY KEY CLUSTERED 
(
	[Distribuidora] ASC,
	[Fuente] ASC,
	[Momento] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_CorteEnre_CargadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[CorteEnre] ADD  CONSTRAINT [DF_Plan_CorteEnre_CargadoEn]  DEFAULT (sysdatetime()) FOR [CargadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CorteEnre_Distribuidora]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CorteEnre]'))
ALTER TABLE [planificacion].[CorteEnre]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CorteEnre_Distribuidora] CHECK  (([Distribuidora]='NORLUZ' OR [Distribuidora]='VOLTARA'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CorteEnre_Distribuidora]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CorteEnre]'))
ALTER TABLE [planificacion].[CorteEnre] CHECK CONSTRAINT [CK_Plan_CorteEnre_Distribuidora]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CorteEnre_Fuente]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CorteEnre]'))
ALTER TABLE [planificacion].[CorteEnre]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CorteEnre_Fuente] CHECK  (([Fuente]='github' OR [Fuente]='mapa' OR [Fuente]='ufs'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CorteEnre_Fuente]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CorteEnre]'))
ALTER TABLE [planificacion].[CorteEnre] CHECK CONSTRAINT [CK_Plan_CorteEnre_Fuente]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CorteEnre_Usuarios]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CorteEnre]'))
ALTER TABLE [planificacion].[CorteEnre]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CorteEnre_Usuarios] CHECK  (([UsuariosSinServicio]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CorteEnre_Usuarios]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CorteEnre]'))
ALTER TABLE [planificacion].[CorteEnre] CHECK CONSTRAINT [CK_Plan_CorteEnre_Usuarios]
GO
