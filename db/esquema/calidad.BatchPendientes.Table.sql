-- Table [calidad].[BatchPendientes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[BatchPendientes]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[BatchPendientes](
	[LoteID] [bigint] IDENTITY(1,1) NOT NULL,
	[Entorno] [nvarchar](20) NOT NULL,
	[Estado] [nvarchar](20) NOT NULL,
	[RunID] [nvarchar](50) NULL,
	[ArchivoJsonl] [nvarchar](500) NOT NULL,
	[Bytes] [bigint] NOT NULL,
	[Llamados] [int] NOT NULL,
	[Modelo] [nvarchar](100) NULL,
	[PlantillaID] [int] NULL,
	[UserID] [int] NULL,
	[MetadataJson] [nvarchar](max) NOT NULL,
	[BatchID] [nvarchar](300) NULL,
	[FechaAlta] [datetime2](7) NOT NULL,
	[FechaEnvio] [datetime2](7) NULL,
	[FechaFin] [datetime2](7) NULL,
	[Intentos] [int] NOT NULL,
	[Error] [nvarchar](1000) NULL,
 CONSTRAINT [PK_BatchPendientes] PRIMARY KEY CLUSTERED 
(
	[LoteID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_BatchPendientes_Batch]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[BatchPendientes]') AND name = N'IX_BatchPendientes_Batch')
CREATE NONCLUSTERED INDEX [IX_BatchPendientes_Batch] ON [calidad].[BatchPendientes]
(
	[BatchID] ASC
)
WHERE ([BatchID] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_BatchPendientes_Estado]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[BatchPendientes]') AND name = N'IX_BatchPendientes_Estado')
CREATE NONCLUSTERED INDEX [IX_BatchPendientes_Estado] ON [calidad].[BatchPendientes]
(
	[Entorno] ASC,
	[Estado] ASC,
	[LoteID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_BatchPendientes_Run]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[BatchPendientes]') AND name = N'IX_BatchPendientes_Run')
CREATE NONCLUSTERED INDEX [IX_BatchPendientes_Run] ON [calidad].[BatchPendientes]
(
	[RunID] ASC
)
WHERE ([RunID] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchPendientes_Entorno]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchPendientes] ADD  CONSTRAINT [DF_BatchPendientes_Entorno]  DEFAULT ('prod') FOR [Entorno]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchPendientes_Estado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchPendientes] ADD  CONSTRAINT [DF_BatchPendientes_Estado]  DEFAULT ('PENDIENTE') FOR [Estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchPendientes_Bytes]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchPendientes] ADD  CONSTRAINT [DF_BatchPendientes_Bytes]  DEFAULT ((0)) FOR [Bytes]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchPendientes_Llamados]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchPendientes] ADD  CONSTRAINT [DF_BatchPendientes_Llamados]  DEFAULT ((0)) FOR [Llamados]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchPendientes_Alta]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchPendientes] ADD  CONSTRAINT [DF_BatchPendientes_Alta]  DEFAULT (sysutcdatetime()) FOR [FechaAlta]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchPendientes_Intentos]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchPendientes] ADD  CONSTRAINT [DF_BatchPendientes_Intentos]  DEFAULT ((0)) FOR [Intentos]
END
GO
