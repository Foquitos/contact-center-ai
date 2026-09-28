-- Table [dbo].[acumuladores_de_agentes_por_skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[acumuladores_de_agentes_por_skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[acumuladores_de_agentes_por_skill](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[LOGIN ID] [varchar](max) NULL,
	[AGENTE] [varchar](max) NULL,
	[CAMPAÑA] [varchar](max) NULL,
	[INTERVALO] [datetime] NULL,
	[LOGIN (s)] [int] NULL,
	[AVAIL (s)] [int] NULL,
	[PREVIEW (s)] [int] NULL,
	[DIAL (s)] [int] NULL,
	[RING (s)] [int] NULL,
	[CONNECT (s)] [int] NULL,
	[HOLD (s)] [int] NULL,
	[ACW (s)] [int] NULL,
	[NOT READY (s)] [int] NULL,
	[OTHER (s)] [int] NULL,
	[BREAK (s)] [int] NULL,
	[BAÑO (s)] [int] NULL,
	[ENTRENAMIENTO (s)] [int] NULL,
	[CAPACITACIÓN (s)] [int] NULL,
	[ADMINISTRATIVO (s)] [int] NULL,
	[SUPERVISIÓN (s)] [int] NULL,
	[LLAMADO.SALIENTE (s)] [int] NULL,
	[GESTIÓN.FUERA.LÍNEA (s)] [int] NULL,
	[NO.DISPONIBLE (s)] [int] NULL,
	[TUTORES (s)] [int] NULL,
	[AUXILIAR TOTAL (s)] [int] NULL,
	[AUXILIAR %] [float] NULL,
	[TIEMPO REAL DE LOGUEO] [int] NULL,
	[UTILIZACIÓN] [float] NULL,
	[AHT (s)] [float] NULL,
	[ATT (s)] [float] NULL,
	[ATENDIDAS] [int] NULL,
	[NO ATENDIDAS] [int] NULL,
	[TRANSFER IN] [int] NULL,
	[TRANSFER OUT] [int] NULL,
	[TIPIFICACIÓN EXITOSO] [int] NULL,
	[TIPIFICACIÓN NO EXITOSO] [int] NULL,
	[TIPIFICACIÓN NO EFECTIVO] [int] NULL,
	[TIPIFICACIÓN NEUTRO] [int] NULL,
	[TIPIFICACIÓN OTRO] [int] NULL,
	[CE] [int] NULL,
	[%CE] [float] NULL,
	[EXITOS / CE] [float] NULL,
	[EXITOS POR HORA REAL] [float] NULL,
	[FECHA]  AS (CONVERT([date],[Intervalo])) PERSISTED,
 CONSTRAINT [PK_acumuladores_de_agentes_por_skill] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
