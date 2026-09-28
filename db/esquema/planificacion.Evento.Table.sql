-- Table [planificacion].[Evento]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Evento]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Evento](
	[EventoID] [int] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NULL,
	[Desde] [datetime2](0) NOT NULL,
	[Hasta] [datetime2](0) NOT NULL,
	[Tipo] [nvarchar](40) NOT NULL,
	[Descripcion] [nvarchar](400) NULL,
	[Factor] [decimal](6, 3) NULL,
	[Origen] [nvarchar](20) NOT NULL,
	[ExcluirDeEntrenamiento] [bit] NOT NULL,
	[Confirmado] [bit] NOT NULL,
	[CreadoPor] [int] NULL,
	[CreadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_Evento] PRIMARY KEY CLUSTERED 
(
	[EventoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Plan_Evento_rango]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[planificacion].[Evento]') AND name = N'IX_Plan_Evento_rango')
CREATE NONCLUSTERED INDEX [IX_Plan_Evento_rango] ON [planificacion].[Evento]
(
	[Desde] ASC,
	[Hasta] ASC
)
INCLUDE([CampanaID],[Tipo],[Factor]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Evento_Origen]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Evento] ADD  CONSTRAINT [DF_Plan_Evento_Origen]  DEFAULT ('manual') FOR [Origen]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Evento_Excluir]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Evento] ADD  CONSTRAINT [DF_Plan_Evento_Excluir]  DEFAULT ((1)) FOR [ExcluirDeEntrenamiento]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Evento_Confirmado]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Evento] ADD  CONSTRAINT [DF_Plan_Evento_Confirmado]  DEFAULT ((0)) FOR [Confirmado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Evento_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Evento] ADD  CONSTRAINT [DF_Plan_Evento_Fecha]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Evento_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Evento]'))
ALTER TABLE [planificacion].[Evento]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Evento_Factor] CHECK  (([Factor] IS NULL OR [Factor]>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Evento_Factor]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Evento]'))
ALTER TABLE [planificacion].[Evento] CHECK CONSTRAINT [CK_Plan_Evento_Factor]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Evento_Origen]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Evento]'))
ALTER TABLE [planificacion].[Evento]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Evento_Origen] CHECK  (([Origen]='manual' OR [Origen]='auto'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Evento_Origen]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Evento]'))
ALTER TABLE [planificacion].[Evento] CHECK CONSTRAINT [CK_Plan_Evento_Origen]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Evento_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Evento]'))
ALTER TABLE [planificacion].[Evento]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Evento_Rango] CHECK  (([Hasta]>[Desde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Evento_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Evento]'))
ALTER TABLE [planificacion].[Evento] CHECK CONSTRAINT [CK_Plan_Evento_Rango]
GO
