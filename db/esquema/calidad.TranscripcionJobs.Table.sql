-- Table [calidad].[TranscripcionJobs]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[TranscripcionJobs]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[TranscripcionJobs](
	[JobID] [bigint] IDENTITY(1,1) NOT NULL,
	[Entorno] [nvarchar](20) NOT NULL,
	[IdAplicativo] [nvarchar](200) NOT NULL,
	[Estado] [nvarchar](20) NOT NULL,
	[Motor] [nvarchar](30) NOT NULL,
	[BatchID] [nvarchar](300) NULL,
	[SegmentID] [int] NULL,
	[Modelo] [nvarchar](100) NULL,
	[SolicitadoPor] [int] NULL,
	[FechaSolicitud] [datetime2](7) NOT NULL,
	[FechaEnvio] [datetime2](7) NULL,
	[FechaFin] [datetime2](7) NULL,
	[Intentos] [int] NOT NULL,
	[Error] [nvarchar](1000) NULL,
 CONSTRAINT [PK_TranscripcionJobs] PRIMARY KEY CLUSTERED 
(
	[JobID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_TranscripcionJobs_Batch]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[TranscripcionJobs]') AND name = N'IX_TranscripcionJobs_Batch')
CREATE NONCLUSTERED INDEX [IX_TranscripcionJobs_Batch] ON [calidad].[TranscripcionJobs]
(
	[BatchID] ASC
)
WHERE ([BatchID] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_TranscripcionJobs_Estado]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[TranscripcionJobs]') AND name = N'IX_TranscripcionJobs_Estado')
CREATE NONCLUSTERED INDEX [IX_TranscripcionJobs_Estado] ON [calidad].[TranscripcionJobs]
(
	[Entorno] ASC,
	[Estado] ASC,
	[JobID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_TranscripcionJobs_Id]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[TranscripcionJobs]') AND name = N'IX_TranscripcionJobs_Id')
CREATE NONCLUSTERED INDEX [IX_TranscripcionJobs_Id] ON [calidad].[TranscripcionJobs]
(
	[Entorno] ASC,
	[IdAplicativo] ASC,
	[JobID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [UQ_TranscripcionJobs_Abierto]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[TranscripcionJobs]') AND name = N'UQ_TranscripcionJobs_Abierto')
CREATE UNIQUE NONCLUSTERED INDEX [UQ_TranscripcionJobs_Abierto] ON [calidad].[TranscripcionJobs]
(
	[Entorno] ASC,
	[IdAplicativo] ASC
)
WHERE ([Estado] IN ('PENDIENTE', 'ENVIANDO', 'ENVIADO'))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_TranscripcionJobs_Entorno]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[TranscripcionJobs] ADD  CONSTRAINT [DF_TranscripcionJobs_Entorno]  DEFAULT ('prod') FOR [Entorno]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_TranscripcionJobs_Estado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[TranscripcionJobs] ADD  CONSTRAINT [DF_TranscripcionJobs_Estado]  DEFAULT ('PENDIENTE') FOR [Estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_TranscripcionJobs_Motor]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[TranscripcionJobs] ADD  CONSTRAINT [DF_TranscripcionJobs_Motor]  DEFAULT ('gemini_batch') FOR [Motor]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_TranscripcionJobs_Solicitud]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[TranscripcionJobs] ADD  CONSTRAINT [DF_TranscripcionJobs_Solicitud]  DEFAULT (sysutcdatetime()) FOR [FechaSolicitud]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_TranscripcionJobs_Intentos]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[TranscripcionJobs] ADD  CONSTRAINT [DF_TranscripcionJobs_Intentos]  DEFAULT ((0)) FOR [Intentos]
END
GO
