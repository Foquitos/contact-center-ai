-- Table [dbo].[Vantix_Casos_Etiquetas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vantix_Casos_Etiquetas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vantix_Casos_Etiquetas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[caso_id] [varchar](50) NOT NULL,
	[etiqueta] [varchar](100) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_Vantix_Casos_Etiquetas_CasoID]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Vantix_Casos_Etiquetas]') AND name = N'IX_Vantix_Casos_Etiquetas_CasoID')
CREATE NONCLUSTERED INDEX [IX_Vantix_Casos_Etiquetas_CasoID] ON [dbo].[Vantix_Casos_Etiquetas]
(
	[caso_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
