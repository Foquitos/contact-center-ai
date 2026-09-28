-- Table [dbo].[Ventas Fibra Alcanzo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Ventas Fibra Alcanzo]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Ventas Fibra Alcanzo](
	[ID Registro] [int] NOT NULL,
	[Fecha Operacion] [date] NULL,
	[Cliente DNI/CUIT] [varchar](max) NULL,
	[Producto] [varchar](max) NULL,
	[Megas] [varchar](max) NULL,
	[Fecha Instalacion] [date] NULL,
	[Observaciones] [varchar](max) NULL,
	[Estado] [varchar](max) NULL,
	[Nro Orden] [varchar](max) NULL,
	[Nombre Vendedor] [varchar](max) NULL,
 CONSTRAINT [PK_Ventas Fibra Alcanzo] PRIMARY KEY CLUSTERED 
(
	[ID Registro] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
