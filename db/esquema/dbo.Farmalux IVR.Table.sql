-- Table [dbo].[Farmalux IVR]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Farmalux IVR]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Farmalux IVR](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [datetime] NULL,
	[CALLERID] [bigint] NULL,
	[DNI] [bigint] NULL,
	[Cola] [varchar](max) NULL,
	[Agente] [varchar](max) NULL,
	[Nombre Agente] [varchar](max) NULL,
	[Respuesta 1] [tinyint] NULL,
	[Respuesta 2] [tinyint] NULL,
	[Respuesta 3] [tinyint] NULL,
	[Respuesta 4] [tinyint] NULL,
	[IVR] [varchar](max) NULL,
	[última Acción] [varchar](max) NULL,
 CONSTRAINT [PK_Farmalux IVR] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
