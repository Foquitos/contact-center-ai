-- Table [pagina_web].[RolePermissions]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[RolePermissions]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[RolePermissions](
	[role_id] [int] NOT NULL,
	[permission_id] [int] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[role_id] ASC,
	[permission_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK__RolePermi__permi__5C979F60]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[RolePermissions]'))
ALTER TABLE [pagina_web].[RolePermissions]  WITH CHECK ADD FOREIGN KEY([permission_id])
REFERENCES [pagina_web].[Permissions] ([id])
ON DELETE CASCADE
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK__RolePermi__role___5BA37B27]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[RolePermissions]'))
ALTER TABLE [pagina_web].[RolePermissions]  WITH CHECK ADD FOREIGN KEY([role_id])
REFERENCES [pagina_web].[Roles] ([id])
ON DELETE CASCADE
GO
