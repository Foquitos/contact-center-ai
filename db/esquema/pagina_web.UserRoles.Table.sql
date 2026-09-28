-- Table [pagina_web].[UserRoles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[UserRoles]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[UserRoles](
	[nomina_id] [int] NOT NULL,
	[role_id] [int] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[nomina_id] ASC,
	[role_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [UQ_UserRoles_nomina_role]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[UserRoles]') AND name = N'UQ_UserRoles_nomina_role')
CREATE UNIQUE NONCLUSTERED INDEX [UQ_UserRoles_nomina_role] ON [pagina_web].[UserRoles]
(
	[nomina_id] ASC,
	[role_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK__UserRoles__role___5F740C0B]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[UserRoles]'))
ALTER TABLE [pagina_web].[UserRoles]  WITH CHECK ADD FOREIGN KEY([role_id])
REFERENCES [pagina_web].[Roles] ([id])
ON DELETE CASCADE
GO
