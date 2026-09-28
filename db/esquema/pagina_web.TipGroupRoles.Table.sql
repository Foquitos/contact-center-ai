-- Table [pagina_web].[TipGroupRoles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[TipGroupRoles]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[TipGroupRoles](
	[tip_group_id] [int] NOT NULL,
	[role_id] [int] NOT NULL,
	[created_at] [datetime] NOT NULL,
 CONSTRAINT [PK_TipGroupRoles] PRIMARY KEY CLUSTERED 
(
	[tip_group_id] ASC,
	[role_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_TipGroupRoles_role_id]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[TipGroupRoles]') AND name = N'IX_TipGroupRoles_role_id')
CREATE NONCLUSTERED INDEX [IX_TipGroupRoles_role_id] ON [pagina_web].[TipGroupRoles]
(
	[role_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_TipGroupRoles_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[TipGroupRoles] ADD  CONSTRAINT [DF_TipGroupRoles_created]  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_TipGroupRoles_Roles]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[TipGroupRoles]'))
ALTER TABLE [pagina_web].[TipGroupRoles]  WITH CHECK ADD  CONSTRAINT [FK_TipGroupRoles_Roles] FOREIGN KEY([role_id])
REFERENCES [pagina_web].[Roles] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_TipGroupRoles_Roles]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[TipGroupRoles]'))
ALTER TABLE [pagina_web].[TipGroupRoles] CHECK CONSTRAINT [FK_TipGroupRoles_Roles]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_TipGroupRoles_TipGroups]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[TipGroupRoles]'))
ALTER TABLE [pagina_web].[TipGroupRoles]  WITH CHECK ADD  CONSTRAINT [FK_TipGroupRoles_TipGroups] FOREIGN KEY([tip_group_id])
REFERENCES [pagina_web].[TipGroups] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_TipGroupRoles_TipGroups]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[TipGroupRoles]'))
ALTER TABLE [pagina_web].[TipGroupRoles] CHECK CONSTRAINT [FK_TipGroupRoles_TipGroups]
GO
