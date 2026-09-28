-- Table [pagina_web].[ChatbotDocMarkdown]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMarkdown]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotDocMarkdown](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[titulo] [nvarchar](200) NULL,
	[orden] [int] NOT NULL,
	[contenido_md] [nvarchar](max) NOT NULL,
	[updated_by] [int] NULL,
	[activo] [bit] NOT NULL,
	[created_at] [datetime2](7) NOT NULL,
	[updated_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_ChatbotDocMarkdown] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_ChatbotDocMarkdown_chatbot]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMarkdown]') AND name = N'IX_ChatbotDocMarkdown_chatbot')
CREATE NONCLUSTERED INDEX [IX_ChatbotDocMarkdown_chatbot] ON [pagina_web].[ChatbotDocMarkdown]
(
	[chatbot_id] ASC,
	[activo] ASC
)
INCLUDE([orden]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMarkdown_orden]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMarkdown] ADD  CONSTRAINT [DF_ChatbotDocMarkdown_orden]  DEFAULT ((1)) FOR [orden]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMarkdown_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMarkdown] ADD  CONSTRAINT [DF_ChatbotDocMarkdown_activo]  DEFAULT ((1)) FOR [activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMarkdown_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMarkdown] ADD  CONSTRAINT [DF_ChatbotDocMarkdown_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMarkdown_ua]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMarkdown] ADD  CONSTRAINT [DF_ChatbotDocMarkdown_ua]  DEFAULT (sysdatetime()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocMarkdown_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMarkdown]'))
ALTER TABLE [pagina_web].[ChatbotDocMarkdown]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotDocMarkdown_Chatbot] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocMarkdown_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMarkdown]'))
ALTER TABLE [pagina_web].[ChatbotDocMarkdown] CHECK CONSTRAINT [FK_ChatbotDocMarkdown_Chatbot]
GO
