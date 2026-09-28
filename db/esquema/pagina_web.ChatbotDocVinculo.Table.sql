-- Table [pagina_web].[ChatbotDocVinculo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocVinculo]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotDocVinculo](
	[doc_id] [int] NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[created_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_ChatbotDocVinculo] PRIMARY KEY CLUSTERED 
(
	[doc_id] ASC,
	[chatbot_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_ChatbotDocVinculo_chatbot]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocVinculo]') AND name = N'IX_ChatbotDocVinculo_chatbot')
CREATE NONCLUSTERED INDEX [IX_ChatbotDocVinculo_chatbot] ON [pagina_web].[ChatbotDocVinculo]
(
	[chatbot_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocVinculo_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocVinculo] ADD  CONSTRAINT [DF_ChatbotDocVinculo_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocVinculo_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocVinculo]'))
ALTER TABLE [pagina_web].[ChatbotDocVinculo]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotDocVinculo_Chatbot] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocVinculo_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocVinculo]'))
ALTER TABLE [pagina_web].[ChatbotDocVinculo] CHECK CONSTRAINT [FK_ChatbotDocVinculo_Chatbot]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocVinculo_Doc]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocVinculo]'))
ALTER TABLE [pagina_web].[ChatbotDocVinculo]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotDocVinculo_Doc] FOREIGN KEY([doc_id])
REFERENCES [pagina_web].[ChatbotDocMarkdown] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocVinculo_Doc]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocVinculo]'))
ALTER TABLE [pagina_web].[ChatbotDocVinculo] CHECK CONSTRAINT [FK_ChatbotDocVinculo_Doc]
GO
