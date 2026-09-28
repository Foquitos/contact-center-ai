-- Table [dbo].[Voltara informe agente]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara informe agente]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara informe agente](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Intervalo] [date] NULL,
	[Operación] [varchar](max) NULL,
	[BPO] [varchar](max) NULL,
	[Login] [varchar](max) NULL,
	[volumen de llamadas respondidas] [smallint] NULL,
	[Tiempo Agentes Logueados] [int] NULL,
	[Tiempo Agentes Disponible] [int] NULL,
	[Tiempo total ring] [smallint] NULL,
	[Tiempo Agentes en servicio] [int] NULL,
	[Tiempo Agentes en pausa] [int] NULL,
	[Tiempo Pausa Login] [int] NULL,
	[Tiempo Pausas Capacitación] [smallint] NULL,
	[Tiempo Pausas Administrativo] [smallint] NULL,
	[Tiempo Pausa Técnica] [smallint] NULL,
	[Tiempo Pausa Personal] [smallint] NULL,
	[Llamadas cerradas por el cliente] [smallint] NULL,
	[Tiempo Pausa Break] [smallint] NULL,
	[Tempo total después del servicio] [smallint] NULL,
	[Tiempo Pausa Activa] [smallint] NULL,
	[Tiempo Pausa sin razón] [smallint] NULL,
	[Tiempo total de hold] [smallint] NULL,
	[TMO] [smallint] NULL,
	[% Ocupacion] [float] NULL,
	[Llamadas transferidas] [smallint] NULL,
	[Tasa de transferencias] [float] NULL,
 CONSTRAINT [PK_Voltara informe agente] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
