-- Table [calidad].[AuditoriaVersiones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaVersiones]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditoriaVersiones](
	[VersionAuditoriaID] [int] IDENTITY(1,1) NOT NULL,
	[AuditoriaID] [int] NOT NULL,
	[Numero] [int] NOT NULL,
	[PlantillaVersionID] [int] NULL,
	[PuntajeFinal] [float] NULL,
	[EsErrorCritico] [bit] NULL,
	[FechaAuditoria] [datetime2](3) NULL,
	[SnapshotJSON] [nvarchar](max) NULL,
	[ReauditadaPorUsuarioID] [int] NULL,
	[Motivo] [nvarchar](400) NULL,
	[FechaArchivado] [datetime2](3) NOT NULL,
 CONSTRAINT [PK_AuditoriaVersiones] PRIMARY KEY CLUSTERED 
(
	[VersionAuditoriaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_AuditoriaVersiones_Auditoria_Numero] UNIQUE NONCLUSTERED 
(
	[AuditoriaID] ASC,
	[Numero] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_AuditoriaVersiones_Auditoria]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaVersiones]') AND name = N'IX_AuditoriaVersiones_Auditoria')
CREATE NONCLUSTERED INDEX [IX_AuditoriaVersiones_Auditoria] ON [calidad].[AuditoriaVersiones]
(
	[AuditoriaID] ASC,
	[Numero] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_AuditoriaVersiones_FechaArchivado]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaVersiones]') AND name = N'IX_AuditoriaVersiones_FechaArchivado')
CREATE NONCLUSTERED INDEX [IX_AuditoriaVersiones_FechaArchivado] ON [calidad].[AuditoriaVersiones]
(
	[FechaArchivado] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditoriaVersiones_FechaArchivado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditoriaVersiones] ADD  CONSTRAINT [DF_AuditoriaVersiones_FechaArchivado]  DEFAULT (sysutcdatetime()) FOR [FechaArchivado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaVersiones_Auditoria]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaVersiones]'))
ALTER TABLE [calidad].[AuditoriaVersiones]  WITH CHECK ADD  CONSTRAINT [FK_AuditoriaVersiones_Auditoria] FOREIGN KEY([AuditoriaID])
REFERENCES [calidad].[Auditorias] ([AuditoriaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaVersiones_Auditoria]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaVersiones]'))
ALTER TABLE [calidad].[AuditoriaVersiones] CHECK CONSTRAINT [FK_AuditoriaVersiones_Auditoria]
GO
