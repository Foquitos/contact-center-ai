-- Table [dbo].[Voltara_Salesforce_usuarios]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_Salesforce_usuarios]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_Salesforce_usuarios](
	[clave] [varchar](max) NULL,
	[Nombre completo] [varchar](max) NULL,
	[Usuario] [varchar](max) NULL,
	[Número de identidad] [varchar](max) NULL,
	[DNI] [varchar](max) NULL,
	[fecha_actualizacion] [datetime] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
