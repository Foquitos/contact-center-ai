-- Table [dbo].[Farmalux llamadas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Farmalux llamadas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Farmalux llamadas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha / Hora] [datetime] NULL,
	[Espera] [time](7) NULL,
	[Duración] [time](7) NULL,
	[Número] [varchar](max) NULL,
	[Atendió] [varchar](max) NULL,
	[Cola] [varchar](max) NULL,
	[ Estado] [varchar](max) NULL,
 CONSTRAINT [PK_Farmalux llamadas] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
