-- Table [dbo].[Movilink Acumuladores de Agentes por Skills]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Movilink Acumuladores de Agentes por Skills]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Movilink Acumuladores de Agentes por Skills](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Intervalo] [smalldatetime] NULL,
	[LoginId] [varchar](max) NULL,
	[NombreAgente] [varchar](max) NULL,
	[NombreCampania] [varchar](max) NULL,
	[Login] [smallint] NULL,
	[Avail] [smallint] NULL,
	[Preview] [smallint] NULL,
	[Dial] [smallint] NULL,
	[Ring] [smallint] NULL,
	[Connect] [smallint] NULL,
	[Hold] [smallint] NULL,
	[ACW] [smallint] NULL,
	[NotReady] [smallint] NULL,
	[BREAK] [smallint] NULL,
	[LUNCH (s)] [smallint] NULL,
	[COACHING (s)] [smallint] NULL,
	[ADMINISTRATIVO (s)] [smallint] NULL,
	[BAÑO (s)] [smallint] NULL,
	[LLAMADA_MANUAL (s)] [smallint] NULL,
	[Break6] [smallint] NULL,
	[Break7] [smallint] NULL,
	[Break8] [smallint] NULL,
	[Break9] [smallint] NULL,
	[Other] [smallint] NULL,
	[Atendidas] [smallint] NULL,
	[Abandonadas] [smallint] NULL,
	[TransferIn] [smallint] NULL,
	[TransferOut] [smallint] NULL,
	[rgExitoso] [smallint] NULL,
	[rgNoExitoso] [smallint] NULL,
	[rgNoEfectivo] [smallint] NULL,
	[rgNeutro] [smallint] NULL,
	[rgOtro] [smallint] NULL,
	[idAgente] [smallint] NULL,
	[idCampania] [smallint] NULL,
	[AuxiliarTotal] [smallint] NULL,
	[AuxiliarPorciento] [float] NULL,
	[TiempoRealLogueo] [smallint] NULL,
	[Utilizacion] [float] NULL,
	[AHT] [float] NULL,
	[ATT] [float] NULL,
	[CE] [smallint] NULL,
	[CEPorc] [smallint] NULL,
	[ExitosCE] [float] NULL,
	[ExitosHoraReal] [float] NULL,
 CONSTRAINT [PK_Movilink Acumuladores de Agentes por Skills] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
