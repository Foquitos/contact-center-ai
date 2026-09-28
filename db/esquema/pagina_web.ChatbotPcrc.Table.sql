-- Table [pagina_web].[ChatbotPcrc]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotPcrc]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotPcrc](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[pcrc_normalizado] [nvarchar](100) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[created_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_ChatbotPcrc] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_ChatbotPcrc] UNIQUE NONCLUSTERED 
(
	[pcrc_normalizado] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotPcrc_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotPcrc] ADD  CONSTRAINT [DF_ChatbotPcrc_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotPcrc_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotPcrc]'))
ALTER TABLE [pagina_web].[ChatbotPcrc]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotPcrc_Chatbot] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotPcrc_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotPcrc]'))
ALTER TABLE [pagina_web].[ChatbotPcrc] CHECK CONSTRAINT [FK_ChatbotPcrc_Chatbot]
GO
