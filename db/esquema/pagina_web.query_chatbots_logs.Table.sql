-- Table [pagina_web].[query_chatbots_logs]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[query_chatbots_logs]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[query_chatbots_logs](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[user_id] [int] NULL,
	[effective_campana] [nvarchar](200) NULL,
	[query] [nvarchar](max) NULL,
	[response] [nvarchar](max) NULL,
	[context] [nvarchar](max) NULL,
	[fecha] [datetime] NULL,
	[task_id] [varchar](max) NULL,
	[input_tokens] [int] NULL,
	[output_tokens] [int] NULL,
	[embedding_tokens] [int] NULL,
	[active] [bit] NOT NULL,
	[score_max] [float] NULL,
	[sin_cobertura] [bit] NULL,
	[vacio_id] [int] NULL,
	[query_condensada] [nvarchar](2000) NULL,
 CONSTRAINT [PK_query_chatbots_logs] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_query_logs_sin_clasificar]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[query_chatbots_logs]') AND name = N'IX_query_logs_sin_clasificar')
CREATE NONCLUSTERED INDEX [IX_query_logs_sin_clasificar] ON [pagina_web].[query_chatbots_logs]
(
	[sin_cobertura] ASC,
	[vacio_id] ASC
)
INCLUDE([effective_campana],[fecha]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_query_chatbots_logs_fecha]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[query_chatbots_logs] ADD  CONSTRAINT [DF_query_chatbots_logs_fecha]  DEFAULT (getdate()) FOR [fecha]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_query_chatbots_logs_active]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[query_chatbots_logs] ADD  CONSTRAINT [DF_query_chatbots_logs_active]  DEFAULT ((1)) FOR [active]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_query_chatbots_logs_vacio]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[query_chatbots_logs]'))
ALTER TABLE [pagina_web].[query_chatbots_logs]  WITH CHECK ADD  CONSTRAINT [FK_query_chatbots_logs_vacio] FOREIGN KEY([vacio_id])
REFERENCES [pagina_web].[ChatbotVacios] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_query_chatbots_logs_vacio]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[query_chatbots_logs]'))
ALTER TABLE [pagina_web].[query_chatbots_logs] CHECK CONSTRAINT [FK_query_chatbots_logs_vacio]
GO
