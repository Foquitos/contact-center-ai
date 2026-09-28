-- Table [dbo].[Vitalis_Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis_Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis_Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[agente_nombre] [varchar](50) NULL,
	[fecha] [date] NULL,
	[cant_llamadas] [smallint] NULL,
	[cant_llamadas_no_asignadas] [tinyint] NULL,
	[tiempo_conectado] [int] NULL,
	[tiempo_disponible] [int] NULL,
	[tiempo_en_llamada] [int] NULL,
	[tiempo_hold] [int] NULL,
	[tiempo_acw] [int] NULL,
	[tiempo_invisible] [int] NULL,
	[tiempo_back_office] [int] NULL,
	[tiempo_aux_bano] [int] NULL,
	[tiempo_aux_break] [int] NULL,
	[tiempo_confirmacion_agendas] [int] NULL,
	[tiempo_feedback] [int] NULL,
	[tiempo_problemas_it] [int] NULL,
	[tiempo_tareas_admin] [int] NULL,
	[tiempo_ticketera_interna] [int] NULL,
	[tiempo_whatsapp] [int] NULL,
	[aht_por_gestiones] [int] NULL,
	[aht_por_caso] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
