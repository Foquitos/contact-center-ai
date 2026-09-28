-- Table [dbo].[Vitalis_Llamadas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis_Llamadas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis_Llamadas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[es_entrante] [bit] NULL,
	[caso_id] [bigint] NULL,
	[contacto_id] [bigint] NULL,
	[contacto_nombre] [varchar](150) NULL,
	[estado_llamada] [varchar](50) NULL,
	[ani] [bigint] NULL,
	[dnis] [bigint] NULL,
	[fecha_inicio] [datetime] NULL,
	[fecha_fin] [datetime] NULL,
	[inicio_espera] [datetime] NULL,
	[fin_espera] [datetime] NULL,
	[agente_usuario] [varchar](50) NULL,
	[grupo_atencion] [varchar](255) NULL,
	[subtipo_interaccion] [varchar](255) NULL,
	[tiempo_espera] [int] NULL,
	[tiempo_timbrado] [int] NULL,
	[cant_eventos_hold] [int] NULL,
	[tiempo_total_hold] [int] NULL,
	[tiempo_ivr] [int] NULL,
	[tiempo_encuesta] [int] NULL,
	[tiempo_conversacion] [int] NULL,
	[duracion_total] [int] NULL,
	[cant_llamadas_hijas] [int] NULL,
	[url_grabacion] [varchar](500) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
