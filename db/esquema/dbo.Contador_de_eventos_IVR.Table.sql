-- Table [dbo].[Contador_de_eventos_IVR]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Contador_de_eventos_IVR]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Contador_de_eventos_IVR](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Intervalo] [date] NULL,
	[Nombre] [varchar](max) NULL,
	[Valor] [varchar](max) NULL,
	[Cantidad] [int] NULL,
 CONSTRAINT [PK_Contador_de_eventos_IVR] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
