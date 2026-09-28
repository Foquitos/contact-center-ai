-- Table [calidad].[AuditTasks]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditTasks]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditTasks](
	[task_id] [varchar](255) NOT NULL,
	[status] [varchar](50) NOT NULL,
	[created_at] [datetime] NULL,
	[updated_at] [datetime] NULL,
	[result_data] [nvarchar](max) NULL,
	[error_message] [nvarchar](max) NULL,
	[Entorno] [nvarchar](20) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[task_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditTask__creat__479C827A]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditTasks] ADD  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditTask__updat__4890A6B3]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditTasks] ADD  DEFAULT (getdate()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditTasks_Entorno]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditTasks] ADD  CONSTRAINT [DF_AuditTasks_Entorno]  DEFAULT ('prod') FOR [Entorno]
END
GO
