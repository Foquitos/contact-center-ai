-- Table [dbo].[Aurora Salud Normalizador Tipo Solicitud]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Aurora Salud Normalizador Tipo Solicitud]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Aurora Salud Normalizador Tipo Solicitud](
	[Tipo Solicitud ID] [tinyint] IDENTITY(1,1) NOT NULL,
	[Tipo Solicitud] [varchar](max) NULL,
 CONSTRAINT [PK_Aurora Salud Normalizador Tipo Solicitud] PRIMARY KEY CLUSTERED 
(
	[Tipo Solicitud ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
