-- Table [calidad].[AuditoriaRevisionDetalles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaRevisionDetalles]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditoriaRevisionDetalles](
	[RevisionDetalleID] [bigint] IDENTITY(1,1) NOT NULL,
	[RevisionID] [bigint] NOT NULL,
	[AtributoID] [int] NOT NULL,
	[ValorIA] [nvarchar](max) NULL,
	[ValorHumano] [nvarchar](max) NULL,
	[Coincide] [bit] NOT NULL,
	[Motivo] [nvarchar](1000) NULL,
 CONSTRAINT [PK_AuditoriaRevisionDetalles] PRIMARY KEY CLUSTERED 
(
	[RevisionDetalleID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_AuditoriaRevisionDetalles_Revision_Atributo] UNIQUE NONCLUSTERED 
(
	[RevisionID] ASC,
	[AtributoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_AuditoriaRevisionDetalles_Atributo_Coincide]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaRevisionDetalles]') AND name = N'IX_AuditoriaRevisionDetalles_Atributo_Coincide')
CREATE NONCLUSTERED INDEX [IX_AuditoriaRevisionDetalles_Atributo_Coincide] ON [calidad].[AuditoriaRevisionDetalles]
(
	[AtributoID] ASC,
	[Coincide] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AuditoriaRevisionDetalles_Coincide]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AuditoriaRevisionDetalles] ADD  CONSTRAINT [DF_AuditoriaRevisionDetalles_Coincide]  DEFAULT ((1)) FOR [Coincide]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaRevisionDetalles_Revision]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaRevisionDetalles]'))
ALTER TABLE [calidad].[AuditoriaRevisionDetalles]  WITH CHECK ADD  CONSTRAINT [FK_AuditoriaRevisionDetalles_Revision] FOREIGN KEY([RevisionID])
REFERENCES [calidad].[AuditoriaRevisiones] ([RevisionID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaRevisionDetalles_Revision]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaRevisionDetalles]'))
ALTER TABLE [calidad].[AuditoriaRevisionDetalles] CHECK CONSTRAINT [FK_AuditoriaRevisionDetalles_Revision]
GO
