-- Table [Benefix].[InteraccionesCargas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Benefix].[InteraccionesCargas]') AND type in (N'U'))
BEGIN
CREATE TABLE [Benefix].[InteraccionesCargas](
	[Dia] [date] NOT NULL,
	[Estado] [varchar](10) NOT NULL,
	[Conversaciones] [int] NULL,
	[Filas] [int] NULL,
	[Segundos] [decimal](9, 1) NULL,
	[Intentos] [int] NOT NULL,
	[Error] [nvarchar](1000) NULL,
	[FechaCarga] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_BenefixInteraccionesCargas] PRIMARY KEY CLUSTERED 
(
	[Dia] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Benefix].[DF_BenefixCargas_Intentos]') AND type = 'D')
BEGIN
ALTER TABLE [Benefix].[InteraccionesCargas] ADD  CONSTRAINT [DF_BenefixCargas_Intentos]  DEFAULT ((1)) FOR [Intentos]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Benefix].[DF_BenefixCargas_FechaCarga]') AND type = 'D')
BEGIN
ALTER TABLE [Benefix].[InteraccionesCargas] ADD  CONSTRAINT [DF_BenefixCargas_FechaCarga]  DEFAULT (sysdatetime()) FOR [FechaCarga]
END
GO
