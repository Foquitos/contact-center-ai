-- Table [dbo].[Dental Miami IVR]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Dental Miami IVR]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Dental Miami IVR](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Menú] [varchar](max) NULL,
	[Opción] [smallint] NULL,
	[Cantidad] [smallint] NULL,
	[Porcentaje] [float] NULL,
 CONSTRAINT [PK_Dental Miami IVR] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
