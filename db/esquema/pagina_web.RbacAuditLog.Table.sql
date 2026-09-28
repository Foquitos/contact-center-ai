-- Table [pagina_web].[RbacAuditLog]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[RbacAuditLog]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[RbacAuditLog](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[actor_documento] [int] NOT NULL,
	[action] [varchar](40) NOT NULL,
	[entity_type] [varchar](20) NOT NULL,
	[entity_id] [int] NULL,
	[detail] [nvarchar](max) NULL,
	[created_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_RbacAuditLog] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_RbacAuditLog_created]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[RbacAuditLog]') AND name = N'IX_RbacAuditLog_created')
CREATE NONCLUSTERED INDEX [IX_RbacAuditLog_created] ON [pagina_web].[RbacAuditLog]
(
	[created_at] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_RbacAuditLog_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[RbacAuditLog] ADD  CONSTRAINT [DF_RbacAuditLog_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
