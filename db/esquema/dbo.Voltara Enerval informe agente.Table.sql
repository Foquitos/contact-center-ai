-- Table [dbo].[Voltara Enerval informe agente]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval informe agente]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval informe agente](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Intervalo] [smalldatetime] NULL,
	[Operación] [tinyint] NULL,
	[BPO] [tinyint] NULL,
	[Login] [varchar](max) NULL,
	[volumen de llamadas respondidas] [smallint] NULL,
	[Tiempo Agentes Logueados] [smallint] NULL,
	[Tiempo Agentes Disponible] [smallint] NULL,
	[Tiempo total ring] [smallint] NULL,
	[Tiempo Agentes en servicio] [smallint] NULL,
	[Tiempo Agentes en pausa] [smallint] NULL,
	[Tiempo Pausa Login] [smallint] NULL,
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
	[Tasa de transferencias] [float] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
