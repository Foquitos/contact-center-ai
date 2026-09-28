-- Table [pagina_web].[TipFeedback]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[TipFeedback]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[TipFeedback](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[tip_id] [int] NOT NULL,
	[documento] [int] NOT NULL,
	[created_at] [datetime] NOT NULL,
 CONSTRAINT [PK_TipFeedback] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_TipFeedback_tip_doc] UNIQUE NONCLUSTERED 
(
	[tip_id] ASC,
	[documento] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_TipFeedback_tip_id]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[TipFeedback]') AND name = N'IX_TipFeedback_tip_id')
CREATE NONCLUSTERED INDEX [IX_TipFeedback_tip_id] ON [pagina_web].[TipFeedback]
(
	[tip_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_TipFeedback_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[TipFeedback] ADD  CONSTRAINT [DF_TipFeedback_created]  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_TipFeedback_Tips]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[TipFeedback]'))
ALTER TABLE [pagina_web].[TipFeedback]  WITH CHECK ADD  CONSTRAINT [FK_TipFeedback_Tips] FOREIGN KEY([tip_id])
REFERENCES [pagina_web].[Tips] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_TipFeedback_Tips]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[TipFeedback]'))
ALTER TABLE [pagina_web].[TipFeedback] CHECK CONSTRAINT [FK_TipFeedback_Tips]
GO
