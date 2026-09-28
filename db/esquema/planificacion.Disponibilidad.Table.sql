-- Table [planificacion].[Disponibilidad]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Disponibilidad](
	[DisponibilidadID] [int] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[PoolID] [int] NULL,
	[DiaSemana] [tinyint] NOT NULL,
	[HoraDesde] [tinyint] NOT NULL,
	[HoraHasta] [tinyint] NOT NULL,
	[Factor] [decimal](4, 3) NOT NULL,
	[Origen] [nvarchar](20) NOT NULL,
	[Muestras] [int] NULL,
	[ActualizadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_Disp] PRIMARY KEY CLUSTERED 
(
	[DisponibilidadID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_Plan_Disp] UNIQUE NONCLUSTERED 
(
	[CampanaID] ASC,
	[PoolID] ASC,
	[DiaSemana] ASC,
	[HoraDesde] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Disp_Origen]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Disponibilidad] ADD  CONSTRAINT [DF_Plan_Disp_Origen]  DEFAULT ('medido') FOR [Origen]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Disp_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Disponibilidad] ADD  CONSTRAINT [DF_Plan_Disp_Fecha]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Disp_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Disp_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Disp_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad] CHECK CONSTRAINT [FK_Plan_Disp_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Dia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Disp_Dia] CHECK  (([DiaSemana]>=(0) AND [DiaSemana]<=(7)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Dia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad] CHECK CONSTRAINT [CK_Plan_Disp_Dia]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Disp_Factor] CHECK  (([Factor]>(0) AND [Factor]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad] CHECK CONSTRAINT [CK_Plan_Disp_Factor]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Horas]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Disp_Horas] CHECK  (([HoraDesde]>=(0) AND [HoraDesde]<=(23) AND ([HoraHasta]>=(1) AND [HoraHasta]<=(24)) AND [HoraHasta]>[HoraDesde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Horas]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad] CHECK CONSTRAINT [CK_Plan_Disp_Horas]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Origen]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Disp_Origen] CHECK  (([Origen]='manual' OR [Origen]='medido'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Disp_Origen]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Disponibilidad]'))
ALTER TABLE [planificacion].[Disponibilidad] CHECK CONSTRAINT [CK_Plan_Disp_Origen]
GO
