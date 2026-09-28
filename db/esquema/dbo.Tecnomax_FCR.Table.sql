-- Table [dbo].[Tecnomax_FCR]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Tecnomax_FCR]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Tecnomax_FCR](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[mostrar_nombre] [varchar](255) NULL,
	[equipo_asistencia] [varchar](50) NULL,
	[asignado_a] [varchar](100) NULL,
	[cliente] [varchar](100) NULL,
	[prioridad] [varchar](50) NULL,
	[etapa] [varchar](100) NULL,
	[agrupador_motivo] [varchar](100) NULL,
	[casuistica] [varchar](100) NULL,
	[tienda_name] [varchar](100) NULL,
	[creado_por] [varchar](50) NULL,
	[division] [varchar](50) NULL,
	[fecha_limite] [datetime] NULL,
	[ultima_actualizacion_usuario] [varchar](100) NULL,
	[ultima_actualizacion_autenticacion] [datetime] NULL,
	[primera_fecha_asignada] [datetime] NULL,
	[marca] [varchar](50) NULL,
	[origen_caso] [varchar](100) NULL,
	[etiquetas_nombre] [varchar](255) NULL,
	[motivo_derivacion] [varchar](100) NULL,
	[motivo_registracion] [varchar](100) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
