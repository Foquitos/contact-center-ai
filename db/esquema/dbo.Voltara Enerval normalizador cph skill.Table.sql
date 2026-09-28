-- Table [dbo].[Voltara Enerval normalizador cph skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval normalizador cph skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval normalizador cph skill](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Facturacion] [varchar](max) NULL,
	[CPH] [float] NULL,
	[Skill ID] [int] NULL,
 CONSTRAINT [PK_Voltara Enerval normalizador cph skill] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
