-- Table [dbo].[codigos_por_dia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[codigos_por_dia]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[codigos_por_dia](
	[Documento] [int] NOT NULL,
	[Código] [varchar](20) NOT NULL,
	[Inicio código] [datetime] NOT NULL,
	[Final código] [datetime] NOT NULL,
	[Comentarios] [varchar](max) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
