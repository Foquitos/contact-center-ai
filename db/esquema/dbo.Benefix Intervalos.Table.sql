-- Table [dbo].[Benefix Intervalos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Benefix Intervalos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Benefix Intervalos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Inicio del intervalo] [datetime] NULL,
	[Fin del intervalo] [datetime] NULL,
	[Nombre del agente] [varchar](max) NULL,
	[Conectado] [float] NULL,
	[En la cola] [float] NULL,
	[Inactivo] [float] NULL,
	[Disponible] [float] NULL,
	[Ausente] [float] NULL,
	[Descanso] [float] NULL,
	[Ocupado] [float] NULL,
	[Comida] [float] NULL,
	[No responde] [float] NULL,
	[Fuera de la cola] [float] NULL,
 CONSTRAINT [PK_Benefix Intervalos] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
