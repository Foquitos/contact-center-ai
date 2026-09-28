-- Table [pagina_web].[LoginAudit]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[LoginAudit]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[LoginAudit](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[documento] [int] NULL,
	[event] [varchar](20) NOT NULL,
	[ip_address] [varchar](64) NULL,
	[user_agent] [nvarchar](400) NULL,
	[created_at] [datetime2](0) NOT NULL,
	[last_seen] [datetime2](0) NULL,
 CONSTRAINT [PK_LoginAudit] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_LoginAudit_created_at]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[LoginAudit]') AND name = N'IX_LoginAudit_created_at')
CREATE NONCLUSTERED INDEX [IX_LoginAudit_created_at] ON [pagina_web].[LoginAudit]
(
	[created_at] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_LoginAudit_documento]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[LoginAudit]') AND name = N'IX_LoginAudit_documento')
CREATE NONCLUSTERED INDEX [IX_LoginAudit_documento] ON [pagina_web].[LoginAudit]
(
	[documento] ASC,
	[created_at] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_LoginAudit_event]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[LoginAudit]') AND name = N'IX_LoginAudit_event')
CREATE NONCLUSTERED INDEX [IX_LoginAudit_event] ON [pagina_web].[LoginAudit]
(
	[event] ASC,
	[created_at] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_LoginAudit_created_at]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[LoginAudit] ADD  CONSTRAINT [DF_LoginAudit_created_at]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
