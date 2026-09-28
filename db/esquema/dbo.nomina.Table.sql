-- Table [dbo].[nomina]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[nomina]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[nomina](
	[id] [int] NOT NULL,
	[legajo] [varchar](16) NOT NULL,
	[documento] [int] NOT NULL,
	[nombre] [varchar](50) NOT NULL,
	[apellido] [varchar](50) NOT NULL,
	[fecha_alta] [date] NULL,
	[fecha_piso] [date] NULL,
 CONSTRAINT [PK_nomina] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
