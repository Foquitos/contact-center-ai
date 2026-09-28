-- Table [calidad].[interacciones_CSV]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[interacciones_CSV]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[interacciones_CSV](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[inicio] [datetime] NULL,
	[fin] [datetime] NULL,
	[duracion] [time](7) NULL,
	[ucid_llamado] [varchar](25) NULL,
	[id_llamado] [varchar](19) NULL,
	[id_agente] [int] NULL,
	[extension] [int] NULL,
	[numero_marcado] [varchar](50) NULL,
	[entrante] [bit] NULL,
	[ani] [varchar](25) NULL,
	[dnis] [varchar](25) NULL,
	[digitos_seleccionados] [varchar](max) NULL,
	[uui] [varchar](max) NULL,
	[segmento] [tinyint] NULL,
	[abandonado] [bit] NULL,
	[codigo_cliente] [float] NULL,
	[inicio_llamado] [time](7) NULL,
	[duracion_llamado] [time](7) NULL,
	[origen] [varchar](25) NULL,
	[destino] [varchar](25) NULL,
	[digitos_seleccionados_segmento] [varchar](50) NULL,
	[skill] [smallint] NULL,
	[tiempo_en_cola_llamado] [time](7) NULL,
	[tiempo_de_ring_llamado] [time](7) NULL,
	[tiempo_hablado] [time](7) NULL,
	[tiempo_acw] [time](7) NULL,
	[tiempo_hold] [time](7) NULL,
	[holds] [smallint] NULL,
	[ucid_segmento] [varchar](max) NULL,
	[uui_segmento] [varchar](max) NULL,
	[nombre_ingresante] [varchar](max) NULL,
	[ip] [varchar](max) NULL,
	[transcripcion] [varchar](max) NULL,
	[fecha_subida] [datetime] NULL,
	[Finalizada_por_operador] [bit] NULL,
	[TiempoSilencio_s] [float] NULL,
	[PorcentajeSilencio] [float] NULL,
 CONSTRAINT [PK_interacciones_CSV] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_interacciones_CSV_fecha_subida]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[interacciones_CSV] ADD  CONSTRAINT [DF_interacciones_CSV_fecha_subida]  DEFAULT (getdate()) FOR [fecha_subida]
END
GO
