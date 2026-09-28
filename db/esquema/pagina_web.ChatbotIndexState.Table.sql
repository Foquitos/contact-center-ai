-- Table [pagina_web].[ChatbotIndexState]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexState]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotIndexState](
	[chatbot_id] [int] NOT NULL,
	[environment] [varchar](20) NOT NULL,
	[index_version] [int] NOT NULL,
	[index_status] [varchar](30) NULL,
	[last_indexed_at] [datetime2](7) NULL,
	[updated_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_ChatbotIndexState] PRIMARY KEY CLUSTERED 
(
	[chatbot_id] ASC,
	[environment] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotIndexState_version]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotIndexState] ADD  CONSTRAINT [DF_ChatbotIndexState_version]  DEFAULT ((0)) FOR [index_version]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotIndexState_updated]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotIndexState] ADD  CONSTRAINT [DF_ChatbotIndexState_updated]  DEFAULT (sysdatetime()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotIndexState_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexState]'))
ALTER TABLE [pagina_web].[ChatbotIndexState]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotIndexState_Chatbots] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotIndexState_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotIndexState]'))
ALTER TABLE [pagina_web].[ChatbotIndexState] CHECK CONSTRAINT [FK_ChatbotIndexState_Chatbots]
GO
