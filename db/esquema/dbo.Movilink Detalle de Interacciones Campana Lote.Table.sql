-- Table [dbo].[Movilink Detalle de Interacciones Campana Lote]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Movilink Detalle de Interacciones Campana Lote]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Movilink Detalle de Interacciones Campana Lote](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[NombreEmpresa] [varchar](max) NULL,
	[NombreCampania] [varchar](max) NULL,
	[NombreLote] [varchar](max) NULL,
	[Inicio] [smalldatetime] NULL,
	[idInteraccion] [varchar](max) NULL,
	[segmento] [tinyint] NULL,
	[TipoContacto] [varchar](max) NULL,
	[Cliente] [varchar](max) NULL,
	[Sentido] [varchar](max) NULL,
	[Duracion] [int] NULL,
	[TiempoTarifado] [int] NULL,
	[Preview] [int] NULL,
	[Dialing] [int] NULL,
	[Ringing] [int] NULL,
	[TalkingTime] [int] NULL,
	[Hold] [int] NULL,
	[ACW] [int] NULL,
	[EnCola] [smallint] NULL,
	[ResultadoGestion] [varchar](max) NULL,
	[Sitio] [smallint] NULL,
	[Equipo] [smallint] NULL,
	[CanalIVR] [smallint] NULL,
	[idTarea] [int] NULL,
	[Entrante] [bit] NULL,
	[Derivada] [bit] NULL,
	[Abandonada] [bit] NULL,
	[FlowIn] [bit] NULL,
	[FlowOut] [bit] NULL,
	[TransferIn] [bit] NULL,
	[TransferOut] [bit] NULL,
	[OrigenCorte] [varchar](max) NULL,
	[CT] [varchar](max) NULL,
	[idCausaQ850] [smallint] NULL,
	[CausaQ] [varchar](max) NULL,
	[idEmpresa] [tinyint] NULL,
	[idCampania] [tinyint] NULL,
	[idLote] [smallint] NULL,
	[Troncal] [smallint] NULL,
	[LoginId] [varchar](max) NULL,
	[NombreAgente] [varchar](max) NULL,
	[TiposRG] [varchar](max) NULL,
	[DNIS] [varchar](max) NULL,
 CONSTRAINT [PK_Movilink Detalle de Interacciones Campana Lote] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
