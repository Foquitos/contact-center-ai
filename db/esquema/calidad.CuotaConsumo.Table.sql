-- Table [calidad].[CuotaConsumo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[CuotaConsumo](
	[ConsumoID] [bigint] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[AnioMes] [char](7) NOT NULL,
	[Documento] [int] NULL,
	[Tipo] [nvarchar](20) NOT NULL,
	[Referencia] [nvarchar](120) NOT NULL,
	[Reservado] [int] NOT NULL,
	[Consumido] [int] NULL,
	[Estado] [nvarchar](20) NOT NULL,
	[Motivo] [nvarchar](300) NULL,
	[CreadoEn] [datetime2](0) NOT NULL,
	[ActualizadoEn] [datetime2](0) NOT NULL,
	[CerradoEn] [datetime2](0) NULL,
 CONSTRAINT [PK_CuotaConsumo] PRIMARY KEY CLUSTERED 
(
	[ConsumoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_CuotaConsumo_abiertas]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]') AND name = N'IX_CuotaConsumo_abiertas')
CREATE NONCLUSTERED INDEX [IX_CuotaConsumo_abiertas] ON [calidad].[CuotaConsumo]
(
	[Estado] ASC,
	[ActualizadoEn] ASC
)
INCLUDE([Referencia],[Tipo]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_CuotaConsumo_campana_mes]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]') AND name = N'IX_CuotaConsumo_campana_mes')
CREATE NONCLUSTERED INDEX [IX_CuotaConsumo_campana_mes] ON [calidad].[CuotaConsumo]
(
	[CampanaID] ASC,
	[AnioMes] ASC
)
INCLUDE([Estado],[Reservado],[Consumido],[Documento],[Tipo]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [UX_CuotaConsumo_referencia]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]') AND name = N'UX_CuotaConsumo_referencia')
CREATE UNIQUE NONCLUSTERED INDEX [UX_CuotaConsumo_referencia] ON [calidad].[CuotaConsumo]
(
	[Referencia] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_CuotaConsumo_Estado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[CuotaConsumo] ADD  CONSTRAINT [DF_CuotaConsumo_Estado]  DEFAULT ('RESERVADO') FOR [Estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_CuotaConsumo_CreadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[CuotaConsumo] ADD  CONSTRAINT [DF_CuotaConsumo_CreadoEn]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_CuotaConsumo_ActualizadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[CuotaConsumo] ADD  CONSTRAINT [DF_CuotaConsumo_ActualizadoEn]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaConsumo_Estado]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]'))
ALTER TABLE [calidad].[CuotaConsumo]  WITH CHECK ADD  CONSTRAINT [CK_CuotaConsumo_Estado] CHECK  (([Estado]='LIBERADO' OR [Estado]='CONFIRMADO' OR [Estado]='RESERVADO'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaConsumo_Estado]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]'))
ALTER TABLE [calidad].[CuotaConsumo] CHECK CONSTRAINT [CK_CuotaConsumo_Estado]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaConsumo_Tipo]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]'))
ALTER TABLE [calidad].[CuotaConsumo]  WITH CHECK ADD  CONSTRAINT [CK_CuotaConsumo_Tipo] CHECK  (([Tipo]='transcripcion' OR [Tipo]='auditoria'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaConsumo_Tipo]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaConsumo]'))
ALTER TABLE [calidad].[CuotaConsumo] CHECK CONSTRAINT [CK_CuotaConsumo_Tipo]
GO
