-- Table [pagina_web].[ChatbotTablaFila]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotTablaFila]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotTablaFila](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[tabla_id] [int] NOT NULL,
	[orden] [int] NOT NULL,
	[datos] [nvarchar](max) NOT NULL,
	[busqueda] [nvarchar](1000) NOT NULL,
	[editada_por] [int] NULL,
	[editada_at] [datetime2](3) NULL,
 CONSTRAINT [PK_ChatbotTablaFila] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_ChatbotTablaFila_tabla]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotTablaFila]') AND name = N'IX_ChatbotTablaFila_tabla')
CREATE NONCLUSTERED INDEX [IX_ChatbotTablaFila_tabla] ON [pagina_web].[ChatbotTablaFila]
(
	[tabla_id] ASC,
	[orden] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTablaFila_orden]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTablaFila] ADD  CONSTRAINT [DF_ChatbotTablaFila_orden]  DEFAULT ((0)) FOR [orden]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTablaFila_busq]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTablaFila] ADD  CONSTRAINT [DF_ChatbotTablaFila_busq]  DEFAULT (N'') FOR [busqueda]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotTablaFila_Tabla]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotTablaFila]'))
ALTER TABLE [pagina_web].[ChatbotTablaFila]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotTablaFila_Tabla] FOREIGN KEY([tabla_id])
REFERENCES [pagina_web].[ChatbotTabla] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotTablaFila_Tabla]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotTablaFila]'))
ALTER TABLE [pagina_web].[ChatbotTablaFila] CHECK CONSTRAINT [FK_ChatbotTablaFila_Tabla]
GO
