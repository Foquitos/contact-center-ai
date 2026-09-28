-- Table [calidad].[AuditSchedulerHistory]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditSchedulerHistory]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditSchedulerHistory](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[scheduler_id] [int] NOT NULL,
	[start_time] [datetime] NOT NULL,
	[end_time] [datetime] NULL,
	[status] [varchar](20) NOT NULL,
	[rows_audited] [int] NULL,
	[error_message] [nvarchar](max) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__start__3019FEA4]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulerHistory] ADD  DEFAULT (getdate()) FOR [start_time]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__AuditSche__rows___310E22DD]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditSchedulerHistory] ADD  DEFAULT ((0)) FOR [rows_audited]
END
GO
