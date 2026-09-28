-- Table [calidad].[PlantillaVersiones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[PlantillaVersiones]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[PlantillaVersiones](
	[VersionID] [int] IDENTITY(1,1) NOT NULL,
	[PlantillaID] [int] NOT NULL,
	[Numero] [int] NOT NULL,
	[Hash] [char](64) NOT NULL,
	[SnapshotJSON] [nvarchar](max) NOT NULL,
	[Motivo] [nvarchar](1000) NULL,
	[CreadoPorUsuarioID] [int] NULL,
	[FechaCreacion] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_PlantillaVersiones] PRIMARY KEY CLUSTERED 
(
	[VersionID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_PlantillaVersiones_Plantilla_Hash] UNIQUE NONCLUSTERED 
(
	[PlantillaID] ASC,
	[Hash] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_PlantillaVersiones_Plantilla_Numero] UNIQUE NONCLUSTERED 
(
	[PlantillaID] ASC,
	[Numero] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_PlantillaVersiones_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[PlantillaVersiones] ADD  CONSTRAINT [DF_PlantillaVersiones_Fecha]  DEFAULT (sysutcdatetime()) FOR [FechaCreacion]
END
GO
