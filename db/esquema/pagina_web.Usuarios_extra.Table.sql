-- Table [pagina_web].[Usuarios_extra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[Usuarios_extra]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[Usuarios_extra](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[documento] [int] NOT NULL,
	[nombre] [varchar](50) NOT NULL,
	[apellido] [varchar](50) NOT NULL,
	[campana] [varchar](50) NOT NULL,
 CONSTRAINT [PK_Usuarios_extra] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
