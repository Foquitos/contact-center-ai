-- Table [dbo].[operadores]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[operadores]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[operadores](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[legajo_id] [int] NULL,
	[estado] [bit] NULL,
	[hora_ingreso] [time](7) NULL,
	[hora_salida] [time](7) NULL,
	[franco_1] [tinyint] NULL,
	[franco_2] [tinyint] NULL,
	[puesto_id] [smallint] NULL,
	[campana_id] [smallint] NULL,
	[equipo_id] [int] NULL,
	[sitio_id] [tinyint] NULL,
	[fecha_ultima_capa] [date] NULL,
	[fecha_desde] [datetime] NULL,
	[fecha_hasta] [datetime] NULL,
 CONSTRAINT [PK__operador__3213E83F1BBAB3AE] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_operadores]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[operadores]') AND name = N'IX_operadores')
CREATE NONCLUSTERED INDEX [IX_operadores] ON [dbo].[operadores]
(
	[fecha_desde] DESC,
	[fecha_hasta] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_operadores_estado_puesto]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[operadores]') AND name = N'IX_operadores_estado_puesto')
CREATE NONCLUSTERED INDEX [IX_operadores_estado_puesto] ON [dbo].[operadores]
(
	[estado] ASC,
	[puesto_id] ASC
)
INCLUDE([legajo_id],[campana_id],[equipo_id]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_operadores_legajo_estado]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[operadores]') AND name = N'IX_operadores_legajo_estado')
CREATE NONCLUSTERED INDEX [IX_operadores_legajo_estado] ON [dbo].[operadores]
(
	[legajo_id] ASC,
	[estado] ASC
)
INCLUDE([fecha_desde],[fecha_hasta],[equipo_id]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_operadores_legajo_estado_fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[operadores]') AND name = N'IX_operadores_legajo_estado_fecha')
CREATE NONCLUSTERED INDEX [IX_operadores_legajo_estado_fecha] ON [dbo].[operadores]
(
	[legajo_id] ASC,
	[estado] ASC,
	[fecha_desde] DESC
)
INCLUDE([fecha_hasta],[equipo_id]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, DATA_COMPRESSION = PAGE) ON [PRIMARY]
GO
-- Index [operadores_legajo_id]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[operadores]') AND name = N'operadores_legajo_id')
CREATE NONCLUSTERED INDEX [operadores_legajo_id] ON [dbo].[operadores]
(
	[legajo_id] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF__operadore__fecha__31B762FC]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[operadores] ADD  CONSTRAINT [DF__operadore__fecha__31B762FC]  DEFAULT (getdate()) FOR [fecha_desde]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF__operadore__fecha__30C33EC3]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[operadores] ADD  CONSTRAINT [DF__operadore__fecha__30C33EC3]  DEFAULT (NULL) FOR [fecha_hasta]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_operadores_campanas]') AND parent_object_id = OBJECT_ID(N'[dbo].[operadores]'))
ALTER TABLE [dbo].[operadores]  WITH CHECK ADD  CONSTRAINT [FK_operadores_campanas] FOREIGN KEY([campana_id])
REFERENCES [dbo].[campanas] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_operadores_campanas]') AND parent_object_id = OBJECT_ID(N'[dbo].[operadores]'))
ALTER TABLE [dbo].[operadores] CHECK CONSTRAINT [FK_operadores_campanas]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_operadores_equipos]') AND parent_object_id = OBJECT_ID(N'[dbo].[operadores]'))
ALTER TABLE [dbo].[operadores]  WITH CHECK ADD  CONSTRAINT [FK_operadores_equipos] FOREIGN KEY([equipo_id])
REFERENCES [dbo].[equipos] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_operadores_equipos]') AND parent_object_id = OBJECT_ID(N'[dbo].[operadores]'))
ALTER TABLE [dbo].[operadores] CHECK CONSTRAINT [FK_operadores_equipos]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_operadores_sitios]') AND parent_object_id = OBJECT_ID(N'[dbo].[operadores]'))
ALTER TABLE [dbo].[operadores]  WITH CHECK ADD  CONSTRAINT [FK_operadores_sitios] FOREIGN KEY([sitio_id])
REFERENCES [dbo].[sitios] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_operadores_sitios]') AND parent_object_id = OBJECT_ID(N'[dbo].[operadores]'))
ALTER TABLE [dbo].[operadores] CHECK CONSTRAINT [FK_operadores_sitios]
GO
