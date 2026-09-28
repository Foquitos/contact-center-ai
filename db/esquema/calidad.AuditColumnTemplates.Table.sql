-- Table [calidad].[AuditColumnTemplates]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditColumnTemplates]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditColumnTemplates](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[user_id] [int] NOT NULL,
	[name] [nvarchar](100) NOT NULL,
	[empresa] [int] NULL,
	[campana] [int] NULL,
	[plantilla_id] [int] NULL,
	[columns_json] [nvarchar](max) NOT NULL,
	[created_at] [datetime] NOT NULL,
	[updated_at] [datetime] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_AuditColTpl_UserName] UNIQUE NONCLUSTERED 
(
	[user_id] ASC,
	[name] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_AuditColTpl_Contexto]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditColumnTemplates]') AND name = N'IX_AuditColTpl_Contexto')
CREATE NONCLUSTERED INDEX [IX_AuditColTpl_Contexto] ON [calidad].[AuditColumnTemplates]
(
	[user_id] ASC,
	[empresa] ASC,
	[campana] ASC,
	[plantilla_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditColTpl_created]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditColumnTemplates] ADD  CONSTRAINT [DF_AuditColTpl_created]  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditColTpl_updated]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditColumnTemplates] ADD  CONSTRAINT [DF_AuditColTpl_updated]  DEFAULT (getdate()) FOR [updated_at]
END
GO
