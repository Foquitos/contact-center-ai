-- Table [pagina_web].[ChatbotDocMaterial]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotDocMaterial](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[tipo] [nvarchar](20) NOT NULL,
	[nombre] [nvarchar](400) NULL,
	[mime] [nvarchar](200) NULL,
	[texto] [nvarchar](max) NULL,
	[datos] [varbinary](max) NULL,
	[tamano_bytes] [int] NOT NULL,
	[nota] [nvarchar](1000) NULL,
	[created_by] [int] NULL,
	[created_at] [datetime2](7) NOT NULL,
	[doc_id_destino] [int] NULL,
	[es_actualizacion] [bit] NOT NULL,
 CONSTRAINT [PK_ChatbotDocMaterial] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_ChatbotDocMaterial_bot]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]') AND name = N'IX_ChatbotDocMaterial_bot')
CREATE NONCLUSTERED INDEX [IX_ChatbotDocMaterial_bot] ON [pagina_web].[ChatbotDocMaterial]
(
	[chatbot_id] ASC,
	[id] ASC
)
INCLUDE([tipo],[nombre],[mime],[tamano_bytes],[nota],[doc_id_destino],[created_by],[created_at]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMaterial_tam]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMaterial] ADD  CONSTRAINT [DF_ChatbotDocMaterial_tam]  DEFAULT ((0)) FOR [tamano_bytes]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMaterial_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMaterial] ADD  CONSTRAINT [DF_ChatbotDocMaterial_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotDocMaterial_act]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotDocMaterial] ADD  CONSTRAINT [DF_ChatbotDocMaterial_act]  DEFAULT ((0)) FOR [es_actualizacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocMaterial_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]'))
ALTER TABLE [pagina_web].[ChatbotDocMaterial]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotDocMaterial_Chatbots] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocMaterial_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]'))
ALTER TABLE [pagina_web].[ChatbotDocMaterial] CHECK CONSTRAINT [FK_ChatbotDocMaterial_Chatbots]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocMaterial_Doc]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]'))
ALTER TABLE [pagina_web].[ChatbotDocMaterial]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotDocMaterial_Doc] FOREIGN KEY([doc_id_destino])
REFERENCES [pagina_web].[ChatbotDocMarkdown] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotDocMaterial_Doc]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]'))
ALTER TABLE [pagina_web].[ChatbotDocMaterial] CHECK CONSTRAINT [FK_ChatbotDocMaterial_Doc]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotDocMaterial_tipo]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]'))
ALTER TABLE [pagina_web].[ChatbotDocMaterial]  WITH CHECK ADD  CONSTRAINT [CK_ChatbotDocMaterial_tipo] CHECK  (([tipo]='archivo' OR [tipo]='texto'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotDocMaterial_tipo]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotDocMaterial]'))
ALTER TABLE [pagina_web].[ChatbotDocMaterial] CHECK CONSTRAINT [CK_ChatbotDocMaterial_tipo]
GO
