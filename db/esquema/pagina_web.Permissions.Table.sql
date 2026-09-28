-- Table [pagina_web].[Permissions]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[Permissions]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[Permissions](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[code] [varchar](100) NOT NULL,
	[description] [varchar](255) NULL,
	[created_at] [datetime] NULL,
	[activo] [bit] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
UNIQUE NONCLUSTERED 
(
	[code] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF__Permissio__creat__5402595F]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Permissions] ADD  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Permissions_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Permissions] ADD  CONSTRAINT [DF_Permissions_activo]  DEFAULT ((1)) FOR [activo]
END
GO
