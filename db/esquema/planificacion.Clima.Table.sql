-- Table [planificacion].[Clima]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Clima]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Clima](
	[CampanaID] [int] NOT NULL,
	[Fecha] [date] NOT NULL,
	[TempMax] [decimal](5, 2) NULL,
	[TempMin] [decimal](5, 2) NULL,
	[TempMedia] [decimal](5, 2) NULL,
	[AparenteMax] [decimal](5, 2) NULL,
	[AparenteMin] [decimal](5, 2) NULL,
	[LluviaMm] [decimal](6, 2) NULL,
	[VientoKmh] [decimal](5, 2) NULL,
	[RafagaKmh] [decimal](5, 2) NULL,
	[HumedadPct] [decimal](5, 2) NULL,
	[EsPronostico] [bit] NOT NULL,
	[Origen] [varchar](40) NOT NULL,
	[ActualizadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_Plan_Clima] PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC,
	[Fecha] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Clima_EsPron]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Clima] ADD  CONSTRAINT [DF_Plan_Clima_EsPron]  DEFAULT ((0)) FOR [EsPronostico]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Clima_Act]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Clima] ADD  CONSTRAINT [DF_Plan_Clima_Act]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Clima_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Clima]'))
ALTER TABLE [planificacion].[Clima]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Clima_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Clima_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Clima]'))
ALTER TABLE [planificacion].[Clima] CHECK CONSTRAINT [FK_Plan_Clima_Campana]
GO
