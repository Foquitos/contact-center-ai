-- Table [pagina_web].[Roles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[Roles]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[Roles](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[name] [varchar](50) NOT NULL,
	[description] [varchar](255) NULL,
	[is_super_admin] [bit] NULL,
	[created_at] [datetime] NULL,
	[parent_role_id] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
UNIQUE NONCLUSTERED 
(
	[name] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF__Roles__is_super___57D2EA43]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Roles] ADD  DEFAULT ((0)) FOR [is_super_admin]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF__Roles__created_a__58C70E7C]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Roles] ADD  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_Roles_Parent]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Roles]'))
ALTER TABLE [pagina_web].[Roles]  WITH CHECK ADD  CONSTRAINT [FK_Roles_Parent] FOREIGN KEY([parent_role_id])
REFERENCES [pagina_web].[Roles] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_Roles_Parent]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Roles]'))
ALTER TABLE [pagina_web].[Roles] CHECK CONSTRAINT [FK_Roles_Parent]
GO
