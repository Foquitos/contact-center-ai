-- Table [dbo].[Mitrol Normalizador Tipo Contacto]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Mitrol Normalizador Tipo Contacto]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Mitrol Normalizador Tipo Contacto](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Tipo Contacto] [varchar](max) NULL,
 CONSTRAINT [PK_Mitrol Normalizador Tipo Contacto] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
