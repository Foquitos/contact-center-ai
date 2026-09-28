-- Table [orion].[Silver_Llamadas_Trafico_Colas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Silver_Llamadas_Trafico_Colas]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Silver_Llamadas_Trafico_Colas](
	[Fecha] [date] NULL,
	[Campaña] [varchar](100) NULL,
	[Inbound_Entrantes] [int] NULL,
	[Inbound_Atendidas] [int] NULL,
	[Inbound_Abandonos] [int] NULL,
	[Inbound_Derivadas] [int] NULL,
	[Inbound_SLA_Menor_60s] [int] NULL,
	[Inbound_Espera_Mayor_240s] [int] NULL,
	[Inbound_Seg_Espera] [int] NULL,
	[Inbound_Seg_Ringing] [int] NULL,
	[Inbound_Seg_Hablado] [int] NULL,
	[Inbound_Seg_Hold] [int] NULL,
	[Outbound_Intentos] [int] NULL,
	[Outbound_Atendidas] [int] NULL,
	[Outbound_No_Atendidas] [int] NULL,
	[Outbound_Seg_Hablado] [int] NULL,
	[Outbound_Seg_Hold] [int] NULL,
	[Total_Gestiones_Efectivas] [int] NULL,
	[Total_Seg_Hablado] [int] NULL,
	[FechaProcesamiento] [datetime] NULL,
	[id] [int] IDENTITY(1,1) NOT NULL
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[DF__Silver_Ll__Fecha__19C0A931]') AND type = 'D')
BEGIN
ALTER TABLE [orion].[Silver_Llamadas_Trafico_Colas] ADD  DEFAULT (getdate()) FOR [FechaProcesamiento]
END
GO
