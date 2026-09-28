-- Table [calidad].[AsistenteMensajes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AsistenteMensajes]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AsistenteMensajes](
	[Id] [bigint] IDENTITY(1,1) NOT NULL,
	[ConversacionId] [int] NOT NULL,
	[Rol] [varchar](8) NOT NULL,
	[Texto] [nvarchar](max) NOT NULL,
	[CreadoEn] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_AsistenteMensajes] PRIMARY KEY CLUSTERED 
(
	[Id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_AsistenteMsg_Conv]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AsistenteMensajes]') AND name = N'IX_AsistenteMsg_Conv')
CREATE NONCLUSTERED INDEX [IX_AsistenteMsg_Conv] ON [calidad].[AsistenteMensajes]
(
	[ConversacionId] ASC,
	[Id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AsistenteMsg_Creado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AsistenteMensajes] ADD  CONSTRAINT [DF_AsistenteMsg_Creado]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AsistenteMsg_Conv]') AND parent_object_id = OBJECT_ID(N'[calidad].[AsistenteMensajes]'))
ALTER TABLE [calidad].[AsistenteMensajes]  WITH CHECK ADD  CONSTRAINT [FK_AsistenteMsg_Conv] FOREIGN KEY([ConversacionId])
REFERENCES [calidad].[AsistenteConversaciones] ([Id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AsistenteMsg_Conv]') AND parent_object_id = OBJECT_ID(N'[calidad].[AsistenteMensajes]'))
ALTER TABLE [calidad].[AsistenteMensajes] CHECK CONSTRAINT [FK_AsistenteMsg_Conv]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_AsistenteMsg_Rol]') AND parent_object_id = OBJECT_ID(N'[calidad].[AsistenteMensajes]'))
ALTER TABLE [calidad].[AsistenteMensajes]  WITH CHECK ADD  CONSTRAINT [CK_AsistenteMsg_Rol] CHECK  (([Rol]='bot' OR [Rol]='user'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_AsistenteMsg_Rol]') AND parent_object_id = OBJECT_ID(N'[calidad].[AsistenteMensajes]'))
ALTER TABLE [calidad].[AsistenteMensajes] CHECK CONSTRAINT [CK_AsistenteMsg_Rol]
GO
