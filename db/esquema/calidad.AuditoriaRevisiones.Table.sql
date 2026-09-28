-- Table [calidad].[AuditoriaRevisiones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaRevisiones]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditoriaRevisiones](
	[RevisionID] [bigint] IDENTITY(1,1) NOT NULL,
	[AuditoriaID] [bigint] NOT NULL,
	[RevisorUsuarioID] [int] NOT NULL,
	[PlantillaID] [int] NULL,
	[IdAplicativo] [nvarchar](200) NULL,
	[Comentario] [nvarchar](2000) NULL,
	[FechaRevision] [datetime2](7) NOT NULL,
	[FechaModificacion] [datetime2](7) NULL,
	[PlantillaVersionID] [int] NULL,
 CONSTRAINT [PK_AuditoriaRevisiones] PRIMARY KEY CLUSTERED 
(
	[RevisionID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_AuditoriaRevisiones_Auditoria_Revisor] UNIQUE NONCLUSTERED 
(
	[AuditoriaID] ASC,
	[RevisorUsuarioID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_AuditoriaRevisiones_Plantilla_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaRevisiones]') AND name = N'IX_AuditoriaRevisiones_Plantilla_Fecha')
CREATE NONCLUSTERED INDEX [IX_AuditoriaRevisiones_Plantilla_Fecha] ON [calidad].[AuditoriaRevisiones]
(
	[PlantillaID] ASC,
	[FechaRevision] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditoriaRevisiones_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditoriaRevisiones] ADD  CONSTRAINT [DF_AuditoriaRevisiones_Fecha]  DEFAULT (sysutcdatetime()) FOR [FechaRevision]
END
GO
