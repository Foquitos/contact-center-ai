-- Table [orion].[Silver_Llamadas_Detalle]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Silver_Llamadas_Detalle]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Silver_Llamadas_Detalle](
	[GlobalId] [varchar](50) NULL,
	[EventoId] [varchar](50) NULL,
	[Tipo_Trafico] [varchar](20) NULL,
	[Fecha_Hora] [datetime] NULL,
	[Campaña] [varchar](100) NULL,
	[Cola_Nombre] [varchar](100) NULL,
	[Legajo_Orion] [varchar](50) NULL,
	[Numero_Origen] [varchar](50) NULL,
	[Numero_Destino] [varchar](50) NULL,
	[Resultado_Llamada] [varchar](50) NULL,
	[Motivo_Corte] [varchar](50) NULL,
	[Segundos_En_Cola] [int] NULL,
	[Segundos_Ring] [int] NULL,
	[Segundos_Hablado] [int] NULL,
	[Segundos_Hold] [int] NULL,
	[Cumple_SLA] [int] NULL,
	[Fue_Derivada] [int] NULL,
	[FechaProcesamiento] [datetime] NULL,
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Tipificacion_Categoria] [varchar](255) NULL,
	[Tipificacion_SubCategoria] [varchar](255) NULL,
	[Tipificacion_Comentario] [varchar](max) NULL,
	[Segundos_Duracion_Total] [int] NULL,
	[Segundos_ACW] [int] NULL,
	[Tipificacion_Estado] [varchar](50) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[DF__Silver_Ll__Fecha__319832C2]') AND type = 'D')
BEGIN
ALTER TABLE [orion].[Silver_Llamadas_Detalle] ADD  DEFAULT (getdate()) FOR [FechaProcesamiento]
END
GO
