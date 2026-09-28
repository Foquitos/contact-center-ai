-- Table [dbo].[Gasur Actividad]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Actividad]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Actividad](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente] [varchar](max) NULL,
	[Fecha] [date] NULL,
	[Login] [time](7) NULL,
	[Logout] [time](7) NULL,
 CONSTRAINT [PK_Gasur Actividad] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
