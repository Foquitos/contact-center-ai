-- Table [pagina_web].[ChatbotImagenes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotImagenes]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotImagenes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[nombre] [nvarchar](400) NOT NULL,
	[mime] [nvarchar](100) NOT NULL,
	[datos] [varbinary](max) NOT NULL,
	[tamano_bytes] [int] NOT NULL,
	[ancho] [int] NULL,
	[alto] [int] NULL,
	[descripcion] [nvarchar](1000) NULL,
	[hash_sha256] [varchar](64) NULL,
	[created_by] [int] NULL,
	[created_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_ChatbotImagenes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_ChatbotImagenes_bot]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotImagenes]') AND name = N'IX_ChatbotImagenes_bot')
CREATE NONCLUSTERED INDEX [IX_ChatbotImagenes_bot] ON [pagina_web].[ChatbotImagenes]
(
	[chatbot_id] ASC,
	[id] DESC
)
INCLUDE([nombre],[mime],[tamano_bytes],[ancho],[alto],[descripcion],[hash_sha256],[created_by],[created_at]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ChatbotImagenes_hash]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotImagenes]') AND name = N'IX_ChatbotImagenes_hash')
CREATE NONCLUSTERED INDEX [IX_ChatbotImagenes_hash] ON [pagina_web].[ChatbotImagenes]
(
	[chatbot_id] ASC,
	[hash_sha256] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotImagenes_tam]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotImagenes] ADD  CONSTRAINT [DF_ChatbotImagenes_tam]  DEFAULT ((0)) FOR [tamano_bytes]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotImagenes_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotImagenes] ADD  CONSTRAINT [DF_ChatbotImagenes_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotImagenes_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotImagenes]'))
ALTER TABLE [pagina_web].[ChatbotImagenes]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotImagenes_Chatbots] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotImagenes_Chatbots]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotImagenes]'))
ALTER TABLE [pagina_web].[ChatbotImagenes] CHECK CONSTRAINT [FK_ChatbotImagenes_Chatbots]
GO
