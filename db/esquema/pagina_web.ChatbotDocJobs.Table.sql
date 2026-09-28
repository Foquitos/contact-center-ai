-- Table [pagina_web].[ChatbotDocJobs]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotDocJobs](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[tipo] [nvarchar](20) NOT NULL,
	[chatbot_id] [int] NULL,
	[status] [nvarchar](40) NOT NULL,
	[payload] [nvarchar](max) NULL,
	[resultado] [nvarchar](max) NULL,
	[error] [nvarchar](max) NULL,
	[requested_by] [int] NULL,
	[environment] [varchar](20) NOT NULL,
	[created_at] [datetime2](7) NOT NULL,
	[started_at] [datetime2](7) NULL,
	[finished_at] [datetime2](7) NULL,
 CONSTRAINT [PK_ChatbotDocJobs] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ChatbotDocJobs_cola]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]') AND name = N'IX_ChatbotDocJobs_cola')
CREATE NONCLUSTERED INDEX [IX_ChatbotDocJobs_cola] ON [pagina_web].[ChatbotDocJobs]
(
	[status] ASC,
	[environment] ASC,
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocJobs_status]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocJobs] ADD  CONSTRAINT [DF_ChatbotDocJobs_status]  DEFAULT ('pending') FOR [status]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocJobs_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocJobs] ADD  CONSTRAINT [DF_ChatbotDocJobs_created]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocJobs_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]'))
ALTER TABLE [pagina_web].[ChatbotDocJobs]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotDocJobs_Chatbots] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocJobs_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]'))
ALTER TABLE [pagina_web].[ChatbotDocJobs] CHECK CONSTRAINT [FK_ChatbotDocJobs_Chatbots]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotDocJobs_status]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]'))
ALTER TABLE [pagina_web].[ChatbotDocJobs]  WITH CHECK ADD  CONSTRAINT [CK_ChatbotDocJobs_status] CHECK  (([status]='failed' OR [status]='done' OR [status]='running' OR [status]='pending'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotDocJobs_status]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]'))
ALTER TABLE [pagina_web].[ChatbotDocJobs] CHECK CONSTRAINT [CK_ChatbotDocJobs_status]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotDocJobs_tipo]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]'))
ALTER TABLE [pagina_web].[ChatbotDocJobs]  WITH CHECK ADD  CONSTRAINT [CK_ChatbotDocJobs_tipo] CHECK  (([tipo]='tabla' OR [tipo]='merge' OR [tipo]='formatear'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotDocJobs_tipo]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocJobs]'))
ALTER TABLE [pagina_web].[ChatbotDocJobs] CHECK CONSTRAINT [CK_ChatbotDocJobs_tipo]
GO
