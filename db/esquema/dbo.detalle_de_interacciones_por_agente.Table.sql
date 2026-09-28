-- Table [dbo].[detalle_de_interacciones_por_agente]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_agente]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[detalle_de_interacciones_por_agente](
	[idInteraccion] [varchar](255) NULL,
	[Segmento] [smallint] NULL,
	[Tipo Contacto] [varchar](max) NULL,
	[Inicio] [datetime] NULL,
	[LoginId] [nvarchar](100) NULL,
	[Agente] [varchar](max) NULL,
	[Empresa] [nvarchar](100) NULL,
	[Campaña] [nvarchar](100) NULL,
	[Cliente] [varchar](max) NULL,
	[Sentido] [varchar](max) NULL,
	[Duración] [int] NULL,
	[Tiempo Tarifado] [int] NULL,
	[Preview] [int] NULL,
	[Dialing] [int] NULL,
	[Ringing] [int] NULL,
	[TalkingTime] [int] NULL,
	[Hold] [int] NULL,
	[ACW] [int] NULL,
	[EnCola] [int] NULL,
	[Tipificación] [nvarchar](100) NULL,
	[Clase Tipificación] [varchar](max) NULL,
	[CRM] [varchar](max) NULL,
	[Sitio] [tinyint] NULL,
	[Equipo] [smallint] NULL,
	[Troncal] [smallint] NULL,
	[CanalIVR] [smallint] NULL,
	[idTarea] [int] NULL,
	[idEmpresa] [smallint] NULL,
	[idCampania] [smallint] NULL,
	[idAgente] [smallint] NULL,
	[idAgenteCliente] [smallint] NULL,
	[LoginID Agente Cliente] [varchar](max) NULL,
	[Agente Cliente] [varchar](max) NULL,
	[DNIS] [varchar](max) NULL,
	[Entrante] [bit] NULL,
	[Derivada] [bit] NULL,
	[Atendidas] [bit] NULL,
	[Agente No Atendidas] [bit] NULL,
	[Abandonada] [bit] NULL,
	[FlowIn] [bit] NULL,
	[FlowOut] [bit] NULL,
	[TransferIn] [bit] NULL,
	[TransferOut] [bit] NULL,
	[Contexto] [varchar](max) NULL,
	[Origen Corte] [varchar](max) NULL,
	[idCausaQ850] [smallint] NULL,
	[CausaQ850] [varchar](max) NULL,
	[Causa Terminación] [varchar](max) NULL,
	[id] [int] IDENTITY(1,1) NOT NULL,
	[fecha_inicio]  AS (CONVERT([date],[inicio])) PERSISTED,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [idx_inicio_desc]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_agente]') AND name = N'idx_inicio_desc')
CREATE NONCLUSTERED INDEX [idx_inicio_desc] ON [dbo].[detalle_de_interacciones_por_agente]
(
	[Inicio] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [Indice_Detalle_inteacciones_por_agente_idinteraccion_segmento]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_agente]') AND name = N'Indice_Detalle_inteacciones_por_agente_idinteraccion_segmento')
CREATE NONCLUSTERED INDEX [Indice_Detalle_inteacciones_por_agente_idinteraccion_segmento] ON [dbo].[detalle_de_interacciones_por_agente]
(
	[idInteraccion] ASC,
	[Segmento] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_Covering_Empresa_Campaña_Tipificacion]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_agente]') AND name = N'IX_Covering_Empresa_Campaña_Tipificacion')
CREATE NONCLUSTERED INDEX [IX_Covering_Empresa_Campaña_Tipificacion] ON [dbo].[detalle_de_interacciones_por_agente]
(
	[Empresa] ASC,
	[Campaña] ASC
)
INCLUDE([Tipificación]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_detalle_de_interacciones_por_agente_idInteraccion]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_agente]') AND name = N'IX_detalle_de_interacciones_por_agente_idInteraccion')
CREATE NONCLUSTERED INDEX [IX_detalle_de_interacciones_por_agente_idInteraccion] ON [dbo].[detalle_de_interacciones_por_agente]
(
	[idInteraccion] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ARITHABORT ON
SET CONCAT_NULL_YIELDS_NULL ON
SET QUOTED_IDENTIFIER ON
SET ANSI_NULLS ON
SET ANSI_PADDING ON
SET ANSI_WARNINGS ON
SET NUMERIC_ROUNDABORT OFF
GO
-- Index [IX_Lead_Optimization]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_agente]') AND name = N'IX_Lead_Optimization')
CREATE NONCLUSTERED INDEX [IX_Lead_Optimization] ON [dbo].[detalle_de_interacciones_por_agente]
(
	[Campaña] ASC,
	[fecha_inicio] ASC,
	[LoginId] ASC,
	[Inicio] ASC
)
INCLUDE([idInteraccion],[Segmento],[Tipificación],[Duración]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
