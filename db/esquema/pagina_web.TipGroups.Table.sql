-- Table [pagina_web].[TipGroups]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[TipGroups]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[TipGroups](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[name] [varchar](100) NOT NULL,
	[description] [varchar](255) NULL,
	[activo] [bit] NOT NULL,
	[created_at] [datetime] NOT NULL,
	[updated_at] [datetime] NOT NULL,
 CONSTRAINT [PK_TipGroups] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_TipGroups_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[TipGroups] ADD  CONSTRAINT [DF_TipGroups_activo]  DEFAULT ((1)) FOR [activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_TipGroups_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[TipGroups] ADD  CONSTRAINT [DF_TipGroups_created]  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_TipGroups_updated]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[TipGroups] ADD  CONSTRAINT [DF_TipGroups_updated]  DEFAULT (getdate()) FOR [updated_at]
END
GO
