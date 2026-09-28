-- Table [dbo].[Vantix_Casos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vantix_Casos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vantix_Casos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[caso_id] [varchar](50) NOT NULL,
	[canal_origen] [varchar](50) NULL,
	[asunto] [varchar](255) NULL,
	[caso_descripcion] [varchar](255) NULL,
	[contacto_nombre] [varchar](150) NULL,
	[estado_caso] [varchar](50) NULL,
	[grupo_asignado] [varchar](255) NULL,
	[agente_usuario] [varchar](150) NULL,
	[fecha_creacion] [datetime] NULL,
	[fecha_actualizacion] [datetime] NULL,
	[fecha_asignacion] [datetime] NULL,
	[fecha_asignacion_grupo] [datetime] NULL,
	[fecha_resolucion] [datetime] NULL,
	[caso_sentiment] [varchar](50) NULL,
	[canal_cuenta] [varchar](150) NULL,
	[tiempo_espera_wt] [int] NULL,
	[tiempo_manejo_ht] [int] NULL,
	[tiempo_respuesta_rt] [int] NULL,
	[bot_activo] [bit] NULL,
	[bot_atendido] [bit] NULL,
	[bot_resuelto] [bit] NULL,
	[aht_caso] [int] NULL,
	[fecha_vencimiento] [datetime] NULL,
	[fecha_cierre] [datetime] NULL,
	[tipo_caso] [varchar](100) NULL,
	[prioridad] [varchar](50) NULL,
	[sla_estado] [varchar](50) NULL,
	[fecha_primera_resp] [datetime] NULL,
	[fecha_ultima_resp] [datetime] NULL,
	[cant_mensajes] [int] NULL,
	[contacto_email] [varchar](150) NULL,
	[contacto_telefono] [varchar](50) NULL,
	[observaciones_form] [varchar](255) NULL,
	[last_follow] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_Vantix_Casos_CasoID]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Vantix_Casos]') AND name = N'IX_Vantix_Casos_CasoID')
CREATE NONCLUSTERED INDEX [IX_Vantix_Casos_CasoID] ON [dbo].[Vantix_Casos]
(
	[caso_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
