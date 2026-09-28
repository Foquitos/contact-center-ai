-- Table [orion].[Normalizador_Skills]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Normalizador_Skills]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Normalizador_Skills](
	[Skill_Nombre_Original] [varchar](100) NULL,
	[Skill_Nombre] [varchar](100) NULL,
	[Campana] [varchar](100) NULL,
	[ID_acme] [int] IDENTITY(1,1) NOT NULL,
	[ID_orion] [tinyint] NULL
) ON [PRIMARY]
END
GO
