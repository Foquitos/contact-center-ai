-- Table [pagina_web].[IA_Precios]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Precios]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[IA_Precios](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[modelo] [nvarchar](80) NOT NULL,
	[fecha_desde] [date] NOT NULL,
	[input_usd_mtok] [decimal](10, 4) NOT NULL,
	[output_usd_mtok] [decimal](10, 4) NOT NULL,
	[notas] [nvarchar](200) NULL,
	[cached_usd_mtok] [decimal](10, 4) NULL,
 CONSTRAINT [PK_IA_Precios] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UX_IA_Precios_modelo_fecha] UNIQUE NONCLUSTERED 
(
	[modelo] ASC,
	[fecha_desde] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
