-- Table [dbo].[Voltara Enerval informe IVR cargas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval informe IVR cargas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval informe IVR cargas](
	[Dia] [date] NOT NULL,
	[Estado] [varchar](10) NOT NULL,
	[Filas] [int] NULL,
	[Bytes] [bigint] NULL,
	[Alcance] [varchar](20) NULL,
	[Usuario] [varchar](50) NULL,
	[SegGenerar] [decimal](8, 1) NULL,
	[SegDescargar] [decimal](8, 1) NULL,
	[SegInsertar] [decimal](8, 1) NULL,
	[Intentos] [int] NOT NULL,
	[Error] [varchar](1000) NULL,
	[FechaCarga] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_VoltaraEnervalInformeIVRCargas] PRIMARY KEY CLUSTERED 
(
	[Dia] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_VoltaraEnervalInformeIVRCargas_Intentos]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[Voltara Enerval informe IVR cargas] ADD  CONSTRAINT [DF_VoltaraEnervalInformeIVRCargas_Intentos]  DEFAULT ((0)) FOR [Intentos]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_VoltaraEnervalInformeIVRCargas_FechaCarga]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[Voltara Enerval informe IVR cargas] ADD  CONSTRAINT [DF_VoltaraEnervalInformeIVRCargas_FechaCarga]  DEFAULT (sysdatetime()) FOR [FechaCarga]
END
GO
