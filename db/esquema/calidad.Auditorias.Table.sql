-- Table [calidad].[Auditorias]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Auditorias](
	[AuditoriaID] [int] IDENTITY(1,1) NOT NULL,
	[IdAplicativo] [varchar](255) NOT NULL,
	[operadorUsuario] [varchar](255) NULL,
	[CampanaID] [int] NULL,
	[EmpresaID] [int] NULL,
	[PlantillaID] [int] NULL,
	[FechaAuditoria] [datetime2](7) NOT NULL,
	[AuditorUsuarioID] [int] NULL,
	[sentido_interaccion] [varchar](50) NULL,
	[input_tokens] [int] NULL,
	[output_tokens] [int] NULL,
	[thoughts_tokens] [int] NULL,
	[response_thoughts] [nvarchar](max) NULL,
	[fecha_interaccion] [datetime] NULL,
	[extras] [varchar](8000) NULL,
	[PuntajeFinal] [decimal](5, 2) NULL,
	[EsErrorCritico] [bit] NOT NULL,
	[tipificacion_interaccion] [nvarchar](255) NULL,
	[duracion_segundos] [int] NULL,
	[comentario_interaccion] [nvarchar](max) NULL,
	[IsActive] [bit] NOT NULL,
	[PlantillaVersionID] [int] NULL,
	[Incidencia] [nvarchar](40) NULL,
	[OperadorNominaID] [int] NULL,
 CONSTRAINT [PK__Auditori__095694E3C11D143D] PRIMARY KEY CLUSTERED 
(
	[AuditoriaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_Auditorias_Base_Busqueda]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_Base_Busqueda')
CREATE NONCLUSTERED INDEX [IX_Auditorias_Base_Busqueda] ON [calidad].[Auditorias]
(
	[PlantillaID] ASC,
	[IsActive] ASC,
	[FechaAuditoria] DESC
)
INCLUDE([CampanaID],[EmpresaID],[AuditorUsuarioID],[IdAplicativo],[operadorUsuario],[fecha_interaccion]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Auditorias_CampanaID]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_CampanaID')
CREATE NONCLUSTERED INDEX [IX_Auditorias_CampanaID] ON [calidad].[Auditorias]
(
	[CampanaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Auditorias_FechaAuditoria]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_FechaAuditoria')
CREATE NONCLUSTERED INDEX [IX_Auditorias_FechaAuditoria] ON [calidad].[Auditorias]
(
	[FechaAuditoria] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Auditorias_Filtros]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_Filtros')
CREATE NONCLUSTERED INDEX [IX_Auditorias_Filtros] ON [calidad].[Auditorias]
(
	[FechaAuditoria] ASC,
	[PlantillaID] ASC,
	[CampanaID] ASC,
	[AuditorUsuarioID] ASC
)
INCLUDE([IdAplicativo],[operadorUsuario]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_Auditorias_IdAplicativo]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_IdAplicativo')
CREATE NONCLUSTERED INDEX [IX_Auditorias_IdAplicativo] ON [calidad].[Auditorias]
(
	[IdAplicativo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_Auditorias_Incidencia]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_Incidencia')
CREATE NONCLUSTERED INDEX [IX_Auditorias_Incidencia] ON [calidad].[Auditorias]
(
	[Incidencia] ASC
)
INCLUDE([PlantillaID],[FechaAuditoria]) 
WHERE ([Incidencia] IS NOT NULL)
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Auditorias_PlantillaVersion]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Auditorias_PlantillaVersion')
CREATE NONCLUSTERED INDEX [IX_Auditorias_PlantillaVersion] ON [calidad].[Auditorias]
(
	[PlantillaID] ASC,
	[PlantillaVersionID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Calidad_Auditorias_Filtros_Performance]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Calidad_Auditorias_Filtros_Performance')
CREATE NONCLUSTERED INDEX [IX_Calidad_Auditorias_Filtros_Performance] ON [calidad].[Auditorias]
(
	[CampanaID] ASC,
	[EmpresaID] ASC,
	[PlantillaID] ASC,
	[FechaAuditoria] ASC
)
INCLUDE([IdAplicativo],[operadorUsuario],[fecha_interaccion],[extras],[PuntajeFinal],[EsErrorCritico],[AuditorUsuarioID],[sentido_interaccion],[response_thoughts]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_Calidad_Auditorias_Plantilla_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Auditorias]') AND name = N'IX_Calidad_Auditorias_Plantilla_Fecha')
CREATE NONCLUSTERED INDEX [IX_Calidad_Auditorias_Plantilla_Fecha] ON [calidad].[Auditorias]
(
	[PlantillaID] ASC,
	[FechaAuditoria] ASC
)
INCLUDE([AuditorUsuarioID],[CampanaID],[EmpresaID],[IdAplicativo],[operadorUsuario],[sentido_interaccion],[fecha_interaccion]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Auditoria__Fecha__0F582957]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Auditorias] ADD  CONSTRAINT [DF__Auditoria__Fecha__0F582957]  DEFAULT (getdate()) FOR [FechaAuditoria]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_Auditorias_EsEC]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Auditorias] ADD  CONSTRAINT [DF_Auditorias_EsEC]  DEFAULT ((0)) FOR [EsErrorCritico]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_Auditorias_IsActive]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Auditorias] ADD  CONSTRAINT [DF_Auditorias_IsActive]  DEFAULT ((1)) FOR [IsActive]
END
GO
