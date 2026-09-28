-- Table [planificacion].[Campana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Campana]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Campana](
	[CampanaID] [int] NOT NULL,
	[Activa] [bit] NOT NULL,
	[IntervaloMin] [smallint] NOT NULL,
	[MaxOcupacion] [decimal](4, 3) NULL,
	[ShrinkageDefault] [decimal](4, 3) NOT NULL,
	[PacienciaSeg] [int] NULL,
	[Nota] [nvarchar](400) NULL,
	[ActualizadoPor] [int] NULL,
	[ActualizadoEn] [datetime2](0) NOT NULL,
	[PacienciaHorizonteSeg] [smallint] NULL,
	[PacienciaOrigen] [nvarchar](20) NULL,
	[ShrinkageAusentismo] [decimal](4, 3) NULL,
	[ShrinkageCapacitacion] [decimal](4, 3) NULL,
	[ShrinkageOrigen] [nvarchar](20) NULL,
	[ShrinkageMedidoEn] [datetime2](0) NULL,
	[SemanasBase] [smallint] NOT NULL,
	[DiasNivelReciente] [smallint] NOT NULL,
	[ClimaLat] [decimal](9, 6) NULL,
	[ClimaLon] [decimal](9, 6) NULL,
	[ClimaActivo] [bit] NOT NULL,
	[NivelPorTipoDeDia] [bit] NOT NULL,
	[RepartoDerivaDias] [int] NOT NULL,
	[RepartoDerivaTope] [decimal](4, 3) NOT NULL,
	[RepartoTipoDiaDias] [int] NOT NULL,
	[RepartoTipoDiaTope] [decimal](4, 3) NOT NULL,
	[CombinarCliente] [bit] NOT NULL,
	[CombinarClientePesoHabil] [decimal](4, 3) NULL,
	[CombinarClientePesoNoHabil] [decimal](4, 3) NULL,
	[CombinarClienteMedidoEn] [datetime2](0) NULL,
	[NivelGbdt] [bit] NOT NULL,
	[NivelGbdtPeso] [decimal](4, 3) NOT NULL,
	[ClimaElasticidadTipoDia] [bit] NOT NULL,
	[ShrinkageNoHabil] [decimal](5, 4) NULL,
	[ShrinkageFeriado] [decimal](5, 4) NULL,
	[BreakMinPorHora] [decimal](4, 1) NOT NULL,
	[RedondeoAbajoDesde] [tinyint] NULL,
	[RedondeoAbajoHasta] [tinyint] NULL,
	[FeriadoComoSabado] [bit] NOT NULL,
	[PuenteFactor] [decimal](4, 3) NULL,
	[PersistenciaPesoHoy] [decimal](4, 3) NOT NULL,
	[PersistenciaPesoResto] [decimal](4, 3) NOT NULL,
	[PersistenciaDias] [tinyint] NOT NULL,
	[IntradiaDesdeHora] [tinyint] NULL,
 CONSTRAINT [PK_Plan_Campana] PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_Activa]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_Activa]  DEFAULT ((1)) FOR [Activa]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_Intervalo]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_Intervalo]  DEFAULT ((30)) FOR [IntervaloMin]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_Ocupacion]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_Ocupacion]  DEFAULT ((0.850)) FOR [MaxOcupacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_Shrinkage]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_Shrinkage]  DEFAULT ((0.300)) FOR [ShrinkageDefault]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_Fecha]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_SemanasBase]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_SemanasBase]  DEFAULT ((52)) FOR [SemanasBase]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_DiasNivel]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_DiasNivel]  DEFAULT ((28)) FOR [DiasNivelReciente]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_ClimaActivo]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_ClimaActivo]  DEFAULT ((0)) FOR [ClimaActivo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_NivelTipoDia]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_NivelTipoDia]  DEFAULT ((1)) FOR [NivelPorTipoDeDia]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_DerivaDias]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_DerivaDias]  DEFAULT ((7)) FOR [RepartoDerivaDias]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_DerivaTope]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_DerivaTope]  DEFAULT ((0.250)) FOR [RepartoDerivaTope]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_TipoDiaDias]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_TipoDiaDias]  DEFAULT ((0)) FOR [RepartoTipoDiaDias]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_TipoDiaTope]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_TipoDiaTope]  DEFAULT ((0.250)) FOR [RepartoTipoDiaTope]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_CombinarCliente]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_CombinarCliente]  DEFAULT ((0)) FOR [CombinarCliente]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_NivelGbdt]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_NivelGbdt]  DEFAULT ((0)) FOR [NivelGbdt]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_NivelGbdtPeso]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_NivelGbdtPeso]  DEFAULT ((0.400)) FOR [NivelGbdtPeso]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_ClimaElasticidad]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_ClimaElasticidad]  DEFAULT ((0)) FOR [ClimaElasticidadTipoDia]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_Break]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_Break]  DEFAULT ((0)) FOR [BreakMinPorHora]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_FeriadoSabado]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_FeriadoSabado]  DEFAULT ((0)) FOR [FeriadoComoSabado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_PersHoy]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_PersHoy]  DEFAULT ((0)) FOR [PersistenciaPesoHoy]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_PersResto]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_PersResto]  DEFAULT ((0)) FOR [PersistenciaPesoResto]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Campana_PersDias]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Campana] ADD  CONSTRAINT [DF_Plan_Campana_PersDias]  DEFAULT ((0)) FOR [PersistenciaDias]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Campana_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Campana_Campana] FOREIGN KEY([CampanaID])
REFERENCES [calidad].[Campanas] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Campana_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [FK_Plan_Campana_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Deriva]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Deriva] CHECK  (([RepartoDerivaDias]>=(0) AND [RepartoDerivaDias]<=(60) AND ([RepartoDerivaTope]>=(0) AND [RepartoDerivaTope]<=(0.5))))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Deriva]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Deriva]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Intervalo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Intervalo] CHECK  (([IntervaloMin]=(60) OR [IntervaloMin]=(30) OR [IntervaloMin]=(15)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Intervalo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Intervalo]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_NivelGbdtPeso]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_NivelGbdtPeso] CHECK  (([NivelGbdtPeso]>=(0) AND [NivelGbdtPeso]<=(0.6)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_NivelGbdtPeso]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_NivelGbdtPeso]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Ocupacion]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Ocupacion] CHECK  (([MaxOcupacion] IS NULL OR [MaxOcupacion]>(0) AND [MaxOcupacion]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Ocupacion]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Ocupacion]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Paciencia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Paciencia] CHECK  (([PacienciaSeg] IS NULL OR [PacienciaSeg]>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Paciencia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Paciencia]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Persistencia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Persistencia] CHECK  (([PersistenciaPesoHoy]>=(0) AND [PersistenciaPesoHoy]<=(1) AND ([PersistenciaPesoResto]>=(0) AND [PersistenciaPesoResto]<=(1)) AND ([PersistenciaDias]>=(0) AND [PersistenciaDias]<=(30)) AND ([PuenteFactor] IS NULL OR [PuenteFactor]>=(0.1) AND [PuenteFactor]<=(1.5)) AND ([IntradiaDesdeHora] IS NULL OR [IntradiaDesdeHora]>=(6) AND [IntradiaDesdeHora]<=(22))))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Persistencia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Persistencia]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_PesoCliente]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_PesoCliente] CHECK  ((([CombinarClientePesoHabil] IS NULL OR [CombinarClientePesoHabil]>=(0) AND [CombinarClientePesoHabil]<=(0.5)) AND ([CombinarClientePesoNoHabil] IS NULL OR [CombinarClientePesoNoHabil]>=(0) AND [CombinarClientePesoNoHabil]<=(0.5))))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_PesoCliente]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_PesoCliente]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_RedondeoAbajo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_RedondeoAbajo] CHECK  (([RedondeoAbajoDesde] IS NULL AND [RedondeoAbajoHasta] IS NULL OR [RedondeoAbajoDesde]>=(0) AND [RedondeoAbajoDesde]<=(23) AND ([RedondeoAbajoHasta]>=(1) AND [RedondeoAbajoHasta]<=(24)) AND [RedondeoAbajoDesde]<>[RedondeoAbajoHasta]))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_RedondeoAbajo]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_RedondeoAbajo]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Shrinkage]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Shrinkage] CHECK  (([ShrinkageDefault]>=(0) AND [ShrinkageDefault]<(0.95)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Shrinkage]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Shrinkage]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_ShrinkageDia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_ShrinkageDia] CHECK  ((([ShrinkageNoHabil] IS NULL OR [ShrinkageNoHabil]>=(0) AND [ShrinkageNoHabil]<(0.9)) AND ([ShrinkageFeriado] IS NULL OR [ShrinkageFeriado]>=(0) AND [ShrinkageFeriado]<(0.9)) AND ([BreakMinPorHora]>=(0) AND [BreakMinPorHora]<=(15))))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_ShrinkageDia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_ShrinkageDia]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_TipoDia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_TipoDia] CHECK  (([RepartoTipoDiaDias]>=(0) AND [RepartoTipoDiaDias]<=(1100) AND ([RepartoTipoDiaTope]>=(0) AND [RepartoTipoDiaTope]<=(0.5))))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_TipoDia]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_TipoDia]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Ventana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Campana_Ventana] CHECK  (([SemanasBase]>=(4) AND [SemanasBase]<=(156) AND ([DiasNivelReciente]>=(7) AND [DiasNivelReciente]<=(120))))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Campana_Ventana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Campana]'))
ALTER TABLE [planificacion].[Campana] CHECK CONSTRAINT [CK_Plan_Campana_Ventana]
GO
