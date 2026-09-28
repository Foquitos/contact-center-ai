-- Table [calidad].[AuditSchedulers]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditSchedulers]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditSchedulers](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[task_name] [varchar](255) NOT NULL,
	[frecuencia] [varchar](50) NOT NULL,
	[hora_ejecucion] [time](7) NOT NULL,
	[dias_semana] [varchar](50) NULL,
	[empresa] [varchar](50) NOT NULL,
	[campana] [varchar](50) NOT NULL,
	[plantilla_id] [int] NOT NULL,
	[cantidad] [int] NOT NULL,
	[rango_dinamico] [varchar](50) NOT NULL,
	[parametros_json] [nvarchar](max) NULL,
	[send_email] [bit] NULL,
	[email_addresses] [varchar](500) NULL,
	[send_gsheets] [bit] NULL,
	[gsheet_id] [varchar](50) NULL,
	[gsheet_name] [varchar](50) NULL,
	[is_active] [bit] NULL,
	[next_run_time] [datetime] NULL,
	[created_by] [int] NULL,
	[created_at] [datetime] NULL,
	[is_running] [bit] NOT NULL,
	[retry_count] [int] NOT NULL,
	[last_started_at] [datetime] NULL,
	[column_template_id] [int] NULL,
	[Entorno] [nvarchar](20) NOT NULL,
 CONSTRAINT [PK__AuditSch__3213E83F669A88CC] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__send___24A84BF8]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF__AuditSche__send___24A84BF8]  DEFAULT ((0)) FOR [send_email]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__send___259C7031]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF__AuditSche__send___259C7031]  DEFAULT ((0)) FOR [send_gsheets]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__is_ac__2690946A]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF__AuditSche__is_ac__2690946A]  DEFAULT ((1)) FOR [is_active]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__creat__2784B8A3]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF__AuditSche__creat__2784B8A3]  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__is_ru__2B554987]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF__AuditSche__is_ru__2B554987]  DEFAULT ((0)) FOR [is_running]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__retry__2D3D91F9]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF__AuditSche__retry__2D3D91F9]  DEFAULT ((0)) FOR [retry_count]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditSchedulers_Entorno]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulers] ADD  CONSTRAINT [DF_AuditSchedulers_Entorno]  DEFAULT ('prod') FOR [Entorno]
END
GO
