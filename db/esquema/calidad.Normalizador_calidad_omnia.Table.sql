-- Table [calidad].[Normalizador_calidad_omnia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Normalizador_calidad_omnia]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Normalizador_calidad_omnia](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[id_empresa_calidad] [int] NOT NULL,
	[Cliente_omnia] [varchar](50) NOT NULL,
 CONSTRAINT [PK_Normalizador_calidad_omnia] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
