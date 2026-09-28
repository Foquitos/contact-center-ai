-- Table [calidad].[AuditoriaDetalles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditoriaDetalles](
	[AuditoriaDetalleID] [int] IDENTITY(1,1) NOT NULL,
	[AuditoriaID] [int] NOT NULL,
	[ValorResultado] [varchar](8000) NULL,
	[Orden] [int] NULL,
	[AtributoID] [int] NOT NULL,
	[PonderacionAplicada] [decimal](6, 2) NULL,
 CONSTRAINT [PK__Auditori__7E7EFF2443D43904] PRIMARY KEY CLUSTERED 
(
	[AuditoriaDetalleID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_AuditoriaDetalles_AuditoriaID]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]') AND name = N'IX_AuditoriaDetalles_AuditoriaID')
CREATE NONCLUSTERED INDEX [IX_AuditoriaDetalles_AuditoriaID] ON [calidad].[AuditoriaDetalles]
(
	[AuditoriaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Calidad_AuditoriaDetalles_AuditoriaID]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]') AND name = N'IX_Calidad_AuditoriaDetalles_AuditoriaID')
CREATE NONCLUSTERED INDEX [IX_Calidad_AuditoriaDetalles_AuditoriaID] ON [calidad].[AuditoriaDetalles]
(
	[AuditoriaID] ASC
)
INCLUDE([AtributoID],[ValorResultado],[Orden]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaDetalles_Atributos]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]'))
ALTER TABLE [calidad].[AuditoriaDetalles]  WITH CHECK ADD  CONSTRAINT [FK_AuditoriaDetalles_Atributos] FOREIGN KEY([AtributoID])
REFERENCES [calidad].[Atributos] ([AtributoID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaDetalles_Atributos]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]'))
ALTER TABLE [calidad].[AuditoriaDetalles] CHECK CONSTRAINT [FK_AuditoriaDetalles_Atributos]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaDetalles_Auditorias]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]'))
ALTER TABLE [calidad].[AuditoriaDetalles]  WITH CHECK ADD  CONSTRAINT [FK_AuditoriaDetalles_Auditorias] FOREIGN KEY([AuditoriaID])
REFERENCES [calidad].[Auditorias] ([AuditoriaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaDetalles_Auditorias]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaDetalles]'))
ALTER TABLE [calidad].[AuditoriaDetalles] CHECK CONSTRAINT [FK_AuditoriaDetalles_Auditorias]
GO
