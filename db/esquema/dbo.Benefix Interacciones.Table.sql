-- Table [dbo].[Benefix Interacciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Benefix Interacciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Benefix Interacciones](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha_Hora] [datetime] NULL,
	[Usuarios] [varchar](100) NULL,
	[Tipo_Comunicacion] [varchar](20) NULL,
	[Usuarios_Alertados] [varchar](100) NULL,
	[Duracion_Segundos] [int] NULL,
	[Direccion] [varchar](25) NULL,
	[Usuarios_con_Interacciones] [varchar](100) NULL,
	[Cola] [varchar](50) NULL,
	[Conclusion] [varchar](max) NULL,
	[Fecha_Finalizacion] [datetime] NULL,
	[DNIS] [varchar](80) NULL,
	[ID_Conversación] [varchar](60) NULL,
	[Abandonadas] [bit] NULL,
	[Abandonadas_en_Cola] [varchar](20) NULL,
	[Es_Entrante] [bit] NULL,
	[DNIS_de_Sesion] [varchar](max) NULL,
	[Transferidas] [bit] NULL,
	[No_ACD] [bit] NULL,
	[Division] [varchar](30) NULL,
	[Primera_Cola] [varchar](50) NULL,
	[Usuarios_no_Responden] [varchar](80) NULL,
	[Estado_Entrega] [varchar](max) NULL,
	[Iniciador_de_Conversacion] [varchar](50) NULL,
	[Aptitudes] [varchar](100) NULL,
	[Aptitudes_Activas] [varchar](100) NULL,
	[IVR_Total] [int] NULL,
	[Total_Cola] [int] NULL,
	[Total_Alertas] [int] NULL,
	[Conversacion_Total] [int] NULL,
	[Segmentos_de_Conversacion] [int] NULL,
	[Total_ACW] [int] NULL,
	[Manejo_Total] [int] NULL,
	[Transferencias] [tinyint] NULL,
	[Consultas] [tinyint] NULL,
	[Tiempo_Abandono] [int] NULL,
	[Correo_de_voz_Total] [int] NULL,
	[No_Responde] [tinyint] NULL,
	[Nombre_Llamante_de_la_Campaña] [varchar](50) NULL,
	[Tipo_Desconexion] [varchar](20) NULL,
	[Retencion_Total] [int] NULL,
	[Total_segmentos_que_estan_contactando] [float] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
