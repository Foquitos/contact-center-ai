-- Table [calidad].[AuditExecutionLog]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditExecutionLog](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[trigger_source] [nvarchar](20) NOT NULL,
	[scheduler_id] [int] NULL,
	[scheduler_name] [nvarchar](200) NULL,
	[task_id] [nvarchar](100) NULL,
	[modo] [nvarchar](10) NOT NULL,
	[batch_id] [nvarchar](300) NULL,
	[empresa] [nvarchar](100) NULL,
	[campana] [nvarchar](100) NULL,
	[plantilla_id] [int] NULL,
	[user_id] [nvarchar](50) NULL,
	[fecha_desde] [date] NULL,
	[fecha_hasta] [date] NULL,
	[cantidad_solicitada] [int] NULL,
	[filas_auditadas] [int] NULL,
	[filas_error] [int] NULL,
	[input_tokens] [bigint] NULL,
	[output_tokens] [bigint] NULL,
	[thoughts_tokens] [bigint] NULL,
	[status] [nvarchar](20) NOT NULL,
	[error_message] [nvarchar](max) NULL,
	[mail_enviado] [bit] NOT NULL,
	[mail_destinatarios] [nvarchar](500) NULL,
	[gsheet_enviado] [bit] NOT NULL,
	[started_at] [datetime2](7) NOT NULL,
	[finished_at] [datetime2](7) NULL,
	[duration_seconds]  AS (datediff(second,[started_at],[finished_at])),
	[created_at] [datetime2](7) NOT NULL,
	[por_operador] [bit] NOT NULL,
	[por_tipificacion] [bit] NOT NULL,
	[desglose_muestreo] [nvarchar](max) NULL,
	[upload_group_id] [nvarchar](100) NULL,
	[modelo] [nvarchar](80) NULL,
	[run_id] [uniqueidentifier] NOT NULL,
	[id_aplicativos] [nvarchar](max) NULL,
	[nivel_razonamiento] [nvarchar](10) NULL,
	[cached_tokens] [int] NULL,
 CONSTRAINT [PK_AuditExecutionLog] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_AuditExecutionLog_run_id]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND name = N'IX_AuditExecutionLog_run_id')
CREATE NONCLUSTERED INDEX [IX_AuditExecutionLog_run_id] ON [calidad].[AuditExecutionLog]
(
	[run_id] ASC
)
INCLUDE([started_at]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_AuditExecutionLog_scheduler]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND name = N'IX_AuditExecutionLog_scheduler')
CREATE NONCLUSTERED INDEX [IX_AuditExecutionLog_scheduler] ON [calidad].[AuditExecutionLog]
(
	[scheduler_id] ASC,
	[started_at] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_AuditExecutionLog_started]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND name = N'IX_AuditExecutionLog_started')
CREATE NONCLUSTERED INDEX [IX_AuditExecutionLog_started] ON [calidad].[AuditExecutionLog]
(
	[started_at] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_AuditExecutionLog_status]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND name = N'IX_AuditExecutionLog_status')
CREATE NONCLUSTERED INDEX [IX_AuditExecutionLog_status] ON [calidad].[AuditExecutionLog]
(
	[status] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_AuditExecutionLog_upload_group]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND name = N'IX_AuditExecutionLog_upload_group')
CREATE NONCLUSTERED INDEX [IX_AuditExecutionLog_upload_group] ON [calidad].[AuditExecutionLog]
(
	[upload_group_id] ASC
)
WHERE ([upload_group_id] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [UX_AuditExecutionLog_batch_id]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditExecutionLog]') AND name = N'UX_AuditExecutionLog_batch_id')
CREATE UNIQUE NONCLUSTERED INDEX [UX_AuditExecutionLog_batch_id] ON [calidad].[AuditExecutionLog]
(
	[batch_id] ASC
)
WHERE ([batch_id] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_status]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_status]  DEFAULT ('EN_CURSO') FOR [status]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_mail]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_mail]  DEFAULT ((0)) FOR [mail_enviado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_gsheet]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_gsheet]  DEFAULT ((0)) FOR [gsheet_enviado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_started]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_started]  DEFAULT (sysutcdatetime()) FOR [started_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_created]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_created]  DEFAULT (sysutcdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_por_operador]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_por_operador]  DEFAULT ((0)) FOR [por_operador]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_por_tipificacion]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_por_tipificacion]  DEFAULT ((0)) FOR [por_tipificacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditExecutionLog_run_id]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditExecutionLog] ADD  CONSTRAINT [DF_AuditExecutionLog_run_id]  DEFAULT (newid()) FOR [run_id]
END
GO
