-- Table [dbo].[Dental Miami Eficiencia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Dental Miami Eficiencia]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Dental Miami Eficiencia](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Intervalo] [time](7) NULL,
	[Llamadas atendidas hoy] [smallint] NULL,
	[Abandonadas] [smallint] NULL,
	[< 30s] [smallint] NULL,
	[30s - 60s] [smallint] NULL,
	[60s - 90s] [smallint] NULL,
	[ 90s - 120s] [smallint] NULL,
	[ > 120s] [smallint] NULL,
	[Tiempo de media hablado] [time](7) NULL,
	[Tiempo promedio de espera atendidas] [time](7) NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Dental Miami Eficiencia] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
