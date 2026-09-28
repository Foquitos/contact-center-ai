-- Table [dbo].[Voltara Enerval logueos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval logueos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval logueos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[DOCUMENTO] [varchar](50) NOT NULL,
	[CÓDIGO] [varchar](50) NULL,
	[FECHA] [date] NOT NULL,
	[INICIO] [time](7) NULL,
	[FIN] [time](7) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_enerval_asistencia_documento]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval logueos]') AND name = N'IX_enerval_asistencia_documento')
CREATE NONCLUSTERED INDEX [IX_enerval_asistencia_documento] ON [dbo].[Voltara Enerval logueos]
(
	[DOCUMENTO] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_enerval_asistencia_fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval logueos]') AND name = N'IX_enerval_asistencia_fecha')
CREATE NONCLUSTERED INDEX [IX_enerval_asistencia_fecha] ON [dbo].[Voltara Enerval logueos]
(
	[FECHA] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_enerval_asistencia_merge]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval logueos]') AND name = N'IX_enerval_asistencia_merge')
CREATE NONCLUSTERED INDEX [IX_enerval_asistencia_merge] ON [dbo].[Voltara Enerval logueos]
(
	[DOCUMENTO] ASC,
	[FECHA] ASC,
	[INICIO] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
