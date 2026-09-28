-- Table [dbo].[Vitalis_Casos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis_Casos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis_Casos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[canal_origen] [varchar](50) NULL,
	[caso_id] [int] NULL,
	[contacto_id] [bigint] NULL,
	[contacto_nombre] [varchar](150) NULL,
	[estado_caso] [varchar](50) NULL,
	[grupo_asignado] [varchar](255) NULL,
	[agente_usuario] [varchar](50) NULL,
	[fecha_creacion] [datetime] NULL,
	[fecha_actualizacion] [datetime] NULL,
	[fecha_asignacion] [datetime] NULL,
	[fecha_asignacion_grupo] [datetime] NULL,
	[fecha_resolucion] [datetime] NULL,
	[canal_cuenta] [varchar](50) NULL,
	[tiempo_espera_wt] [int] NULL,
	[tiempo_manejo_ht] [int] NULL,
	[tiempo_primera_resp_frt] [int] NULL,
	[tiempo_respuesta_rt] [int] NULL,
	[wt_tiempo_general] [int] NULL,
	[wt_tiempo_laboral] [int] NULL,
	[ht_tiempo_general] [int] NULL,
	[ht_tiempo_laboral] [int] NULL,
	[aht_tiempo_general] [int] NULL,
	[aht_tiempo_laboral] [int] NULL,
	[rst_tiempo_general] [int] NULL,
	[rst_tiempo_laboral] [int] NULL,
	[frt_tiempo_general] [int] NULL,
	[frt_tiempo_laboral] [int] NULL,
	[es_fcr] [bit] NULL,
	[bot_activo] [bit] NULL,
	[bot_atendido] [bit] NULL,
	[bot_resuelto] [bit] NULL,
	[aht_caso] [int] NULL,
	[fecha_vencimiento] [datetime] NULL,
	[fecha_cierre] [datetime] NULL,
	[tipo_caso] [varchar](255) NULL,
	[prioridad] [varchar](50) NULL,
	[sla_estado] [varchar](50) NULL,
	[fecha_primera_resp] [datetime] NULL,
	[fecha_ultima_resp] [datetime] NULL,
	[cant_mensajes] [smallint] NULL,
	[empresa_caso_relacionada] [varchar](50) NULL,
	[contacto_empresa] [varchar](50) NULL,
	[contacto_email] [varchar](150) NULL,
	[contacto_telefono] [bigint] NULL,
	[contacto_id_personal] [bigint] NULL,
	[contacto_domicilio] [varchar](255) NULL,
	[contacto_ciudad] [varchar](50) NULL,
	[empresa_nombre] [varchar](50) NULL,
	[empresa_email] [varchar](50) NULL,
	[empresa_telefono] [bigint] NULL,
	[contacto_obra_social] [varchar](100) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
