-- Table [dbo].[Benefix Estados]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Benefix Estados]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Benefix Estados](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Inicio_Intervalo] [datetime] NULL,
	[Fin_Intervalo] [datetime] NULL,
	[Intervalo_Completo] [bit] NULL,
	[ID_Agente] [varchar](50) NULL,
	[Nombre_Agente] [varchar](50) NULL,
	[Conectado_Segundos] [int] NULL,
	[Fuera_de_Cola] [int] NULL,
	[Disponible] [int] NULL,
	[Ocupado] [int] NULL,
	[Ausente] [int] NULL,
	[Descanso] [int] NULL,
	[Comida] [int] NULL,
	[Sistema_Ausente] [int] NULL,
	[Reunion] [int] NULL,
	[En_Cola] [int] NULL,
	[Inactivo] [int] NULL,
	[No_Responde] [int] NULL,
	[Capacitacion] [int] NULL,
	[Interactuando] [int] NULL,
	[En_Comunicacion] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
