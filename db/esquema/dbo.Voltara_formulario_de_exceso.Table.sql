-- Table [dbo].[Voltara_formulario_de_exceso]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_formulario_de_exceso]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_formulario_de_exceso](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Usuario - MS/ME ÚNICAMENTE] [varchar](max) NULL,
	[Fecha de exceso] [date] NULL,
	[Tiempo de exceso] [int] NULL,
	[Estado] [varchar](max) NULL,
	[Legajo] [smallint] NULL,
 CONSTRAINT [PK_Voltara_formulario_de_exceso] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
