-- Table [dbo].[detalle_de_interacciones_por_campana_lote]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_campana_lote]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[detalle_de_interacciones_por_campana_lote](
	[Empresa] [varchar](50) NULL,
	[Campaña] [varchar](100) NULL,
	[Lote] [varchar](100) NULL,
	[Inicio] [datetime] NULL,
	[idInteraccion] [varchar](50) NULL,
	[Segmento] [smallint] NULL,
	[Tipo Contacto] [varchar](50) NULL,
	[Cliente] [varchar](150) NULL,
	[DNIS] [varchar](100) NULL,
	[Sentido] [varchar](50) NULL,
	[LoginId] [varchar](50) NULL,
	[Nombre Agente] [varchar](100) NULL,
	[idCliente] [varchar](200) NULL,
	[Nombre Cliente] [float] NULL,
	[Duración] [int] NULL,
	[Tiempo Tarifado] [int] NULL,
	[Preview] [int] NULL,
	[Dialing] [int] NULL,
	[Ringing] [int] NULL,
	[TalkingTime] [int] NULL,
	[Hold] [int] NULL,
	[ACW] [int] NULL,
	[EnCola] [int] NULL,
	[Tipificación] [varchar](255) NULL,
	[Tipos Tipificación] [varchar](50) NULL,
	[CRM] [varchar](500) NULL,
	[Sitio] [tinyint] NULL,
	[Equipo] [smallint] NULL,
	[Troncal] [smallint] NULL,
	[Canal IVR] [smallint] NULL,
	[idTarea] [int] NULL,
	[Entrante] [bit] NULL,
	[Derivada] [bit] NULL,
	[Atendidas] [bit] NULL,
	[Agente No Atendidas] [bit] NULL,
	[Abandonada] [bit] NULL,
	[FlowIn] [bit] NULL,
	[FlowOut] [bit] NULL,
	[TransferIn] [bit] NULL,
	[TransferOut] [bit] NULL,
	[Origen Corte] [varchar](50) NULL,
	[Contexto] [varchar](100) NULL,
	[Causa Terminación] [varchar](150) NULL,
	[idCausaQ850] [smallint] NULL,
	[CausaQ850] [varchar](100) NULL,
	[idEmpresa] [smallint] NULL,
	[idCampania] [smallint] NULL,
	[idLote] [smallint] NULL,
	[fecha_inicio]  AS (CONVERT([date],[inicio])) PERSISTED
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_CampanaLote_idInteraccion_Segmento]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_campana_lote]') AND name = N'IX_CampanaLote_idInteraccion_Segmento')
CREATE NONCLUSTERED INDEX [IX_CampanaLote_idInteraccion_Segmento] ON [dbo].[detalle_de_interacciones_por_campana_lote]
(
	[idInteraccion] ASC,
	[Segmento] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ddicl_Cliente_Inicio_Incl_idInt_Seg]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_interacciones_por_campana_lote]') AND name = N'IX_ddicl_Cliente_Inicio_Incl_idInt_Seg')
CREATE NONCLUSTERED INDEX [IX_ddicl_Cliente_Inicio_Incl_idInt_Seg] ON [dbo].[detalle_de_interacciones_por_campana_lote]
(
	[Cliente] ASC,
	[Inicio] ASC
)
INCLUDE([idInteraccion],[Segmento]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
