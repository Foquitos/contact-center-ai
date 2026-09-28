-- Table [dbo].[Movilink Acumuladores de Agentes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Movilink Acumuladores de Agentes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Movilink Acumuladores de Agentes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Intervalo] [smalldatetime] NULL,
	[NombreGrupo] [varchar](max) NULL,
	[LoginId] [varchar](max) NULL,
	[NombreAgente] [varchar](max) NULL,
	[Login] [varchar](max) NULL,
	[InternasEntrantesAbandonadas] [tinyint] NULL,
	[InternasEntrantesAtendidas] [tinyint] NULL,
	[InternasSalientesNoAtendidas] [tinyint] NULL,
	[InternasSalientesAtendidas] [tinyint] NULL,
	[EntrantesAbandonadas] [tinyint] NULL,
	[EntrantesAtendidas] [tinyint] NULL,
	[SalientesNoAtendidas] [tinyint] NULL,
	[SalientesAtendidas] [tinyint] NULL,
	[DiscadorAbandonadas] [tinyint] NULL,
	[DiscadorAtendidas] [tinyint] NULL,
	[TransferIn] [tinyint] NULL,
	[TransferOut] [tinyint] NULL,
	[Unstaffed] [smallint] NULL,
	[Avail] [smallint] NULL,
	[Preview] [tinyint] NULL,
	[Dial] [tinyint] NULL,
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
	[Break6] [varchar](max) NULL,
	[Break7] [varchar](max) NULL,
	[Break8] [varchar](max) NULL,
	[Break9] [varchar](max) NULL,
	[AuxiliarTotal] [smallint] NULL,
	[AuxiliarPorciento] [float] NULL,
	[TiempoRealLogueo] [varchar](max) NULL,
	[Utilizacion] [float] NULL,
	[ConnectReal] [float] NULL,
	[RingReal] [float] NULL,
	[AvailReal] [float] NULL,
	[AHT] [float] NULL,
	[ATT] [float] NULL,
	[AvailCount] [smallint] NULL,
	[HoldCount] [tinyint] NULL,
	[NotReadyCount] [tinyint] NULL,
	[AvailMax] [smallint] NULL,
	[HoldMax] [smallint] NULL,
	[NotReadyMax] [smallint] NULL,
	[TalkingTimeInternasEntrantes] [smallint] NULL,
	[TalkingTimeInternasSalientes] [smallint] NULL,
	[rgOtro] [smallint] NULL,
	[rgExitoso] [smallint] NULL,
	[rgNoExitoso] [smallint] NULL,
	[rgNoEfectivo] [smallint] NULL,
	[rgNeutro] [smallint] NULL,
	[CE] [smallint] NULL,
	[CEPorc] [smallint] NULL,
	[ExitosCE] [float] NULL,
	[ExitosHoraReal] [float] NULL,
	[idAgente] [varchar](max) NULL,
 CONSTRAINT [PK_Movilink Acumuladores de Agentes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
