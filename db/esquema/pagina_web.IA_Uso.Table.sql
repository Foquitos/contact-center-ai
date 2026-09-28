-- Table [pagina_web].[IA_Uso]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Uso]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[IA_Uso](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[fecha] [datetime2](7) NOT NULL,
	[feature] [nvarchar](40) NOT NULL,
	[modo] [nvarchar](10) NOT NULL,
	[modelo] [nvarchar](80) NOT NULL,
	[user_id] [int] NULL,
	[input_tokens] [bigint] NULL,
	[output_tokens] [bigint] NULL,
	[thoughts_tokens] [bigint] NULL,
	[cached_tokens] [bigint] NULL,
	[embedding_tokens] [bigint] NULL,
	[campana_id] [int] NULL,
	[empresa_id] [int] NULL,
	[ref_id] [nvarchar](120) NULL,
	[status] [nvarchar](20) NULL,
	[extras] [nvarchar](max) NULL,
	[created_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_IA_Uso] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_IA_Uso_feature_fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Uso]') AND name = N'IX_IA_Uso_feature_fecha')
CREATE NONCLUSTERED INDEX [IX_IA_Uso_feature_fecha] ON [pagina_web].[IA_Uso]
(
	[feature] ASC,
	[fecha] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_IA_Uso_fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Uso]') AND name = N'IX_IA_Uso_fecha')
CREATE NONCLUSTERED INDEX [IX_IA_Uso_fecha] ON [pagina_web].[IA_Uso]
(
	[fecha] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_IA_Uso_modelo_fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Uso]') AND name = N'IX_IA_Uso_modelo_fecha')
CREATE NONCLUSTERED INDEX [IX_IA_Uso_modelo_fecha] ON [pagina_web].[IA_Uso]
(
	[modelo] ASC,
	[fecha] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [UX_IA_Uso_feature_ref]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Uso]') AND name = N'UX_IA_Uso_feature_ref')
CREATE UNIQUE NONCLUSTERED INDEX [UX_IA_Uso_feature_ref] ON [pagina_web].[IA_Uso]
(
	[feature] ASC,
	[ref_id] ASC
)
WHERE ([ref_id] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Uso_modo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Uso] ADD  CONSTRAINT [DF_IA_Uso_modo]  DEFAULT ('sync') FOR [modo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Uso_status]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Uso] ADD  CONSTRAINT [DF_IA_Uso_status]  DEFAULT ('ok') FOR [status]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Uso_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Uso] ADD  CONSTRAINT [DF_IA_Uso_created]  DEFAULT (sysutcdatetime()) FOR [created_at]
END
GO
