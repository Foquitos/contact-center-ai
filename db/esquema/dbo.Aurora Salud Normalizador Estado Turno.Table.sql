-- Table [dbo].[Aurora Salud Normalizador Estado Turno]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Aurora Salud Normalizador Estado Turno]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Aurora Salud Normalizador Estado Turno](
	[Estado Turno ID] [smallint] IDENTITY(1,1) NOT NULL,
	[Estado Turno] [varchar](max) NULL,
 CONSTRAINT [PK_Aurora Salud Normalizador Estado Turno] PRIMARY KEY CLUSTERED 
(
	[Estado Turno ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
