-- Table [pagina_web].[ChatbotIndexJobs]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexJobs]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotIndexJobs](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[status] [nvarchar](20) NOT NULL,
	[requested_by] [int] NULL,
	[error] [nvarchar](max) NULL,
	[created_at] [datetime2](7) NOT NULL,
	[started_at] [datetime2](7) NULL,
	[finished_at] [datetime2](7) NULL,
	[environment] [varchar](20) NOT NULL,
 CONSTRAINT [PK_ChatbotIndexJobs] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ChatbotIndexJobs_env_status]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexJobs]') AND name = N'IX_ChatbotIndexJobs_env_status')
CREATE NONCLUSTERED INDEX [IX_ChatbotIndexJobs_env_status] ON [pagina_web].[ChatbotIndexJobs]
(
	[environment] ASC,
	[status] ASC
)
INCLUDE([chatbot_id]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ChatbotIndexJobs_status]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexJobs]') AND name = N'IX_ChatbotIndexJobs_status')
CREATE NONCLUSTERED INDEX [IX_ChatbotIndexJobs_status] ON [pagina_web].[ChatbotIndexJobs]
(
	[status] ASC
)
INCLUDE([chatbot_id]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_CIJ_status]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotIndexJobs] ADD  CONSTRAINT [DF_CIJ_status]  DEFAULT ('pending') FOR [status]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_CIJ_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotIndexJobs] ADD  CONSTRAINT [DF_CIJ_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotIndexJobs_environment]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotIndexJobs] ADD  CONSTRAINT [DF_ChatbotIndexJobs_environment]  DEFAULT ('prod') FOR [environment]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotIndexJobs_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexJobs]'))
ALTER TABLE [pagina_web].[ChatbotIndexJobs]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotIndexJobs_Chatbot] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotIndexJobs_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexJobs]'))
ALTER TABLE [pagina_web].[ChatbotIndexJobs] CHECK CONSTRAINT [FK_ChatbotIndexJobs_Chatbot]
GO
