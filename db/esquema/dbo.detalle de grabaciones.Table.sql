-- Table [dbo].[detalle de grabaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[detalle de grabaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[detalle de grabaciones](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[idInteraccion] [varchar](50) NULL,
	[Segmento] [tinyint] NOT NULL,
	[FileName] [varchar](127) NULL,
	[FilePath] [int] NOT NULL,
	[Extension] [int] NOT NULL,
	[Grabacion] [bit] NULL,
	[Mail] [bit] NULL,
	[Chat] [bit] NULL,
 CONSTRAINT [PK_detalle de grabaciones] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ddg_idInteraccion_chat_Incl_Seg]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle de grabaciones]') AND name = N'IX_ddg_idInteraccion_chat_Incl_Seg')
CREATE NONCLUSTERED INDEX [IX_ddg_idInteraccion_chat_Incl_Seg] ON [dbo].[detalle de grabaciones]
(
	[idInteraccion] ASC,
	[Chat] ASC
)
INCLUDE([Segmento]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
