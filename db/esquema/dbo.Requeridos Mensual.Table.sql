-- Table [dbo].[Requeridos Mensual]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Requeridos Mensual]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Requeridos Mensual](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Campana ID] [varchar](max) NULL,
	[Requerido de hs] [smallint] NULL,
 CONSTRAINT [PK_Requeridos Mensuales] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
