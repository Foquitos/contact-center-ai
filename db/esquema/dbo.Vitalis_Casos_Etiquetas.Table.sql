-- Table [dbo].[Vitalis_Casos_Etiquetas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis_Casos_Etiquetas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis_Casos_Etiquetas](
	[caso_id] [bigint] NOT NULL,
	[etiqueta] [varchar](200) NOT NULL
) ON [PRIMARY]
END
GO
