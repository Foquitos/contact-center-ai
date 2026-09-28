-- Table [dbo].[CSV_ADH_Skills_Agentes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV_ADH_Skills_Agentes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV_ADH_Skills_Agentes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[inicio_intervalo] [datetime] NULL,
	[fin_intervalo] [datetime] NULL,
	[nombre_skill] [varchar](50) NULL,
	[nombre_agente] [varchar](50) NULL,
	[id_skill] [smallint] NULL,
	[id_agente] [int] NULL,
	[tiempo_acd] [smallint] NULL,
	[tiempo_acw] [smallint] NULL,
	[tiempo_otro] [smallint] NULL,
	[tiempo_ring] [smallint] NULL,
	[tiempo_aux_total] [smallint] NULL,
	[tiempo_disponible] [smallint] NULL,
	[tiempo_logueo] [smallint] NULL,
	[tiempo_hold] [smallint] NULL,
	[tiempo_saliente_extn] [smallint] NULL,
	[tiempo_aux_0] [smallint] NULL,
	[tiempo_aux_1] [smallint] NULL,
	[tiempo_aux_2] [smallint] NULL,
	[tiempo_aux_3] [smallint] NULL,
	[tiempo_aux_4] [smallint] NULL,
	[tiempo_aux_5] [smallint] NULL,
	[tiempo_aux_6] [smallint] NULL,
	[tiempo_aux_7] [smallint] NULL,
	[tiempo_aux_8] [smallint] NULL,
	[tiempo_aux_9] [smallint] NULL,
	[cant_llamadas_acd] [smallint] NULL,
	[cant_llamadas_liberadas] [smallint] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
