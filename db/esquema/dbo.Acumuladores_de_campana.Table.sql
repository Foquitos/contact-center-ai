-- Table [dbo].[Acumuladores_de_campana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Acumuladores_de_campana]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Acumuladores_de_campana](
	[Intervalo] [datetime] NULL,
	[NombreCampania] [varchar](max) NULL,
	[Disparadas] [int] NULL,
	[NoEjecutadas] [int] NULL,
	[NoContacto] [smallint] NULL,
	[Contactadas] [smallint] NULL,
	[NCOcupadas] [smallint] NULL,
	[NCNoContesta] [smallint] NULL,
	[NCOtros] [smallint] NULL,
	[NCMODEMFAX] [smallint] NULL,
	[NCContestador] [smallint] NULL,
	[NCNoHablo] [smallint] NULL,
	[Ingresadas] [smallint] NULL,
	[FlowIn] [smallint] NULL,
	[Derivadas] [smallint] NULL,
	[Abandonadas] [smallint] NULL,
	[FlowOut] [smallint] NULL,
	[NoDerivadas] [smallint] NULL,
	[Avail] [int] NULL,
	[Preview] [smallint] NULL,
	[Dial] [smallint] NULL,
	[Ring] [smallint] NULL,
	[Connect] [int] NULL,
	[Hold] [smallint] NULL,
	[ACW] [smallint] NULL,
	[NotReady] [smallint] NULL,
	[Break0] [int] NULL,
	[Break1] [smallint] NULL,
	[Break2] [smallint] NULL,
	[Break3] [int] NULL,
	[Break4] [smallint] NULL,
	[Break5] [int] NULL,
	[Break6] [smallint] NULL,
	[Break7] [smallint] NULL,
	[Break8] [smallint] NULL,
	[Break9] [smallint] NULL,
	[Other] [int] NULL,
	[Login] [int] NULL,
	[AbandonadasEnRinging] [smallint] NULL,
	[AgentesAtendidas] [smallint] NULL,
	[AgentesAbandonadas] [smallint] NULL,
	[TransferIn] [smallint] NULL,
	[TransferOut] [smallint] NULL,
	[eiEnCola] [int] NULL,
	[AuxiliarTotal] [int] NULL,
	[AuxiliarPorciento] [float] NULL,
	[TiempoRealLogueo] [int] NULL,
	[Utilizacion] [float] NULL,
	[ConnectReal] [float] NULL,
	[RingReal] [float] NULL,
	[AvailReal] [float] NULL,
	[AHT] [float] NULL,
	[ATT] [float] NULL,
	[rgOtro] [smallint] NULL,
	[rgExitoso] [smallint] NULL,
	[rgNoExitoso] [smallint] NULL,
	[rgNoEfectivo] [smallint] NULL,
	[rgNeutro] [smallint] NULL,
	[idCampania] [smallint] NULL,
	[CE] [smallint] NULL,
	[CEPorc] [float] NULL,
	[ExitosCE] [float] NULL,
	[ExitosHoraReal] [float] NULL,
	[fecha_inicio]  AS (CONVERT([date],[intervalo])) PERSISTED
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [NonClusteredIndex-20260102-153442]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Acumuladores_de_campana]') AND name = N'NonClusteredIndex-20260102-153442')
CREATE NONCLUSTERED INDEX [NonClusteredIndex-20260102-153442] ON [dbo].[Acumuladores_de_campana]
(
	[Intervalo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
