-- Table [calidad].[AudioAuditoria]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AudioAuditoria]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AudioAuditoria](
	[AudioID] [bigint] IDENTITY(1,1) NOT NULL,
	[Entorno] [nvarchar](20) NOT NULL,
	[IdAplicativo] [nvarchar](200) NOT NULL,
	[NombreArchivo] [nvarchar](400) NOT NULL,
	[FormatoMime] [nvarchar](60) NOT NULL,
	[TamanioBytes] [bigint] NOT NULL,
	[DuracionSegundos] [float] NULL,
	[FechaCreacion] [datetime2](7) NOT NULL,
	[FechaUltimoAcceso] [datetime2](7) NULL,
	[Fijado] [bit] NOT NULL,
 CONSTRAINT [PK_AudioAuditoria] PRIMARY KEY CLUSTERED 
(
	[AudioID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_AudioAuditoria_Entorno_Id] UNIQUE NONCLUSTERED 
(
	[Entorno] ASC,
	[IdAplicativo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_AudioAuditoria_Entorno_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AudioAuditoria]') AND name = N'IX_AudioAuditoria_Entorno_Fecha')
CREATE NONCLUSTERED INDEX [IX_AudioAuditoria_Entorno_Fecha] ON [calidad].[AudioAuditoria]
(
	[Entorno] ASC,
	[FechaCreacion] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_AudioAuditoria_Entorno_Fecha_NoFijado]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AudioAuditoria]') AND name = N'IX_AudioAuditoria_Entorno_Fecha_NoFijado')
CREATE NONCLUSTERED INDEX [IX_AudioAuditoria_Entorno_Fecha_NoFijado] ON [calidad].[AudioAuditoria]
(
	[Entorno] ASC,
	[FechaCreacion] ASC
)
WHERE ([Fijado]=(0))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AudioAuditoria_Entorno]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AudioAuditoria] ADD  CONSTRAINT [DF_AudioAuditoria_Entorno]  DEFAULT ('prod') FOR [Entorno]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AudioAuditoria_Mime]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AudioAuditoria] ADD  CONSTRAINT [DF_AudioAuditoria_Mime]  DEFAULT ('audio/ogg') FOR [FormatoMime]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AudioAuditoria_Creacion]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AudioAuditoria] ADD  CONSTRAINT [DF_AudioAuditoria_Creacion]  DEFAULT (sysutcdatetime()) FOR [FechaCreacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AudioAuditoria_Fijado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AudioAuditoria] ADD  CONSTRAINT [DF_AudioAuditoria_Fijado]  DEFAULT ((0)) FOR [Fijado]
END
GO
