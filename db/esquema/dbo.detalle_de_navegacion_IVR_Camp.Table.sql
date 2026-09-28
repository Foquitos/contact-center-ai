-- Table [dbo].[detalle_de_navegacion_IVR_Camp]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[detalle_de_navegacion_IVR_Camp]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[detalle_de_navegacion_IVR_Camp](
	[Empresa] [varchar](max) NULL,
	[Campaña] [varchar](max) NULL,
	[Inicio] [datetime] NULL,
	[idInteraccion] [varchar](max) NULL,
	[Segmento] [float] NULL,
	[Tipo Contacto] [varchar](max) NULL,
	[Cliente] [varchar](max) NULL,
	[DNIS] [varchar](max) NULL,
	[Sentido] [varchar](max) NULL,
	[Duración] [float] NULL,
	[Tiempo Tarifado] [float] NULL,
	[Tipificación] [varchar](max) NULL,
	[Tipos Tipificación] [varchar](max) NULL,
	[CRM] [varchar](max) NULL,
	[Sitio] [float] NULL,
	[Equipo] [float] NULL,
	[Troncal] [float] NULL,
	[Canal IVR] [float] NULL,
	[idTarea] [float] NULL,
	[IVR Fecha] [varchar](max) NULL,
	[IVR Nombre] [varchar](max) NULL,
	[IVR Valor] [varchar](max) NULL,
	[idEmpresa] [float] NULL,
	[idCampania] [float] NULL,
	[idAgente] [float] NULL,
	[idGrupo] [float] NULL,
	[fecha_inicio]  AS (CONVERT([date],[inicio])) PERSISTED,
	[ID] [int] IDENTITY(1,1) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
