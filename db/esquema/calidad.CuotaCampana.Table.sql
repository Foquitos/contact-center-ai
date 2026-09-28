-- Table [calidad].[CuotaCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[CuotaCampana]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[CuotaCampana](
	[CampanaID] [int] NOT NULL,
	[LimiteMensual] [int] NULL,
	[LimiteMensualUsuario] [int] NULL,
	[Activo] [bit] NOT NULL,
	[Nota] [nvarchar](400) NULL,
	[ActualizadoPor] [int] NULL,
	[ActualizadoEn] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_CuotaCampana] PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_CuotaCampana_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[CuotaCampana] ADD  CONSTRAINT [DF_CuotaCampana_Activo]  DEFAULT ((1)) FOR [Activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_CuotaCampana_ActualizadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[CuotaCampana] ADD  CONSTRAINT [DF_CuotaCampana_ActualizadoEn]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_CuotaCampana_Campana]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaCampana]'))
ALTER TABLE [calidad].[CuotaCampana]  WITH CHECK ADD  CONSTRAINT [FK_CuotaCampana_Campana] FOREIGN KEY([CampanaID])
REFERENCES [calidad].[Campanas] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_CuotaCampana_Campana]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaCampana]'))
ALTER TABLE [calidad].[CuotaCampana] CHECK CONSTRAINT [FK_CuotaCampana_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaCampana_Limite]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaCampana]'))
ALTER TABLE [calidad].[CuotaCampana]  WITH CHECK ADD  CONSTRAINT [CK_CuotaCampana_Limite] CHECK  (([LimiteMensual] IS NULL OR [LimiteMensual]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaCampana_Limite]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaCampana]'))
ALTER TABLE [calidad].[CuotaCampana] CHECK CONSTRAINT [CK_CuotaCampana_Limite]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaCampana_LimiteU]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaCampana]'))
ALTER TABLE [calidad].[CuotaCampana]  WITH CHECK ADD  CONSTRAINT [CK_CuotaCampana_LimiteU] CHECK  (([LimiteMensualUsuario] IS NULL OR [LimiteMensualUsuario]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_CuotaCampana_LimiteU]') AND parent_object_id = OBJECT_ID(N'[calidad].[CuotaCampana]'))
ALTER TABLE [calidad].[CuotaCampana] CHECK CONSTRAINT [CK_CuotaCampana_LimiteU]
GO
