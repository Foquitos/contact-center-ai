-- Table [dbo].[Gasur Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente] [varchar](max) NULL,
	[Tareas] [int] NULL,
	[Varios] [int] NULL,
	[30 minutos] [int] NULL,
	[10 minutos] [int] NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Gasur Auxiliares] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
