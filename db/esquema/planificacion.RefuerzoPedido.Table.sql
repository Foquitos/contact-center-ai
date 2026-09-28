-- Table [planificacion].[RefuerzoPedido]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[RefuerzoPedido](
	[RefuerzoPedidoID] [int] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[PoolID] [int] NOT NULL,
	[Dia] [date] NOT NULL,
	[Desde] [datetime2](0) NOT NULL,
	[Hasta] [datetime2](0) NOT NULL,
	[FaltantePico] [int] NOT NULL,
	[HorasOperador] [decimal](9, 2) NOT NULL,
	[Accion] [varchar](20) NOT NULL,
	[Estado] [varchar](20) NOT NULL,
	[HorasCubiertas] [decimal](9, 2) NULL,
	[Nota] [nvarchar](400) NULL,
	[CreadoPor] [int] NULL,
	[CreadoEn] [datetime2](0) NOT NULL,
	[ActualizadoPor] [int] NULL,
	[ActualizadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_RefuerzoPedido] PRIMARY KEY CLUSTERED 
(
	[RefuerzoPedidoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Plan_RefuerzoPedido_Campana_Dia]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]') AND name = N'IX_Plan_RefuerzoPedido_Campana_Dia')
CREATE NONCLUSTERED INDEX [IX_Plan_RefuerzoPedido_Campana_Dia] ON [planificacion].[RefuerzoPedido]
(
	[CampanaID] ASC,
	[Dia] ASC
)
INCLUDE([PoolID],[Desde],[Hasta],[Estado],[HorasOperador],[HorasCubiertas]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_RefuerzoPedido_Estado]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[RefuerzoPedido] ADD  CONSTRAINT [DF_Plan_RefuerzoPedido_Estado]  DEFAULT ('pedido') FOR [Estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_RefuerzoPedido_CreadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[RefuerzoPedido] ADD  CONSTRAINT [DF_Plan_RefuerzoPedido_CreadoEn]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_RefuerzoPedido_ActualizadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[RefuerzoPedido] ADD  CONSTRAINT [DF_Plan_RefuerzoPedido_ActualizadoEn]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_RefuerzoPedido_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido]  WITH CHECK ADD  CONSTRAINT [FK_Plan_RefuerzoPedido_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_RefuerzoPedido_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido] CHECK CONSTRAINT [FK_Plan_RefuerzoPedido_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_RefuerzoPedido_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido]  WITH CHECK ADD  CONSTRAINT [FK_Plan_RefuerzoPedido_Pool] FOREIGN KEY([PoolID])
REFERENCES [planificacion].[Pool] ([PoolID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_RefuerzoPedido_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido] CHECK CONSTRAINT [FK_Plan_RefuerzoPedido_Pool]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Estado]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido]  WITH CHECK ADD  CONSTRAINT [CK_Plan_RefuerzoPedido_Estado] CHECK  (([Estado]='descartado' OR [Estado]='no_cubierto' OR [Estado]='cubierto_parcial' OR [Estado]='cubierto' OR [Estado]='pedido'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Estado]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido] CHECK CONSTRAINT [CK_Plan_RefuerzoPedido_Estado]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Faltante]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido]  WITH CHECK ADD  CONSTRAINT [CK_Plan_RefuerzoPedido_Faltante] CHECK  (([FaltantePico]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Faltante]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido] CHECK CONSTRAINT [CK_Plan_RefuerzoPedido_Faltante]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Horas]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido]  WITH CHECK ADD  CONSTRAINT [CK_Plan_RefuerzoPedido_Horas] CHECK  (([HorasOperador]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Horas]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido] CHECK CONSTRAINT [CK_Plan_RefuerzoPedido_Horas]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido]  WITH CHECK ADD  CONSTRAINT [CK_Plan_RefuerzoPedido_Rango] CHECK  (([Hasta]>[Desde]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_RefuerzoPedido_Rango]') AND parent_object_id = OBJECT_ID(N'[planificacion].[RefuerzoPedido]'))
ALTER TABLE [planificacion].[RefuerzoPedido] CHECK CONSTRAINT [CK_Plan_RefuerzoPedido_Rango]
GO
