-- Table [dbo].[Farmalux logueos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Farmalux logueos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Farmalux logueos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente] [varchar](max) NULL,
	[Fecha] [date] NULL,
	[Tiempo Total Logueado] [varchar](50) NULL,
	[Tiempo en Pausa] [time](7) NULL,
	[Tiempo en Registro De LLamada] [time](7) NULL,
	[Tiempo en Banio Break] [time](7) NULL,
	[Tiempo en Capacitacion Reunion] [time](7) NULL,
	[Tiempo en MercadoLibre] [time](7) NULL,
	[Total Entrante] [smallint] NULL,
	[Total Saliente] [smallint] NULL,
	[Tiempo Hablado Entrante] [time](7) NULL,
	[Tiempo Hablado Saleinte en Estado] [time](7) NULL,
	[Tiempo Hablado Saliente Libre] [time](7) NULL,
	[Tiempo Hablado Saliente Total] [time](7) NULL,
 CONSTRAINT [PK_Farmalux logueos] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
