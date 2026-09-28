-- Table [dbo].[Ventas Movilink Alcanzo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Ventas Movilink Alcanzo]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Ventas Movilink Alcanzo](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[ID Linea] [varchar](max) NULL,
	[Portabilidad Nro.] [varchar](max) NULL,
	[Fecha Operacion] [date] NULL,
	[Cliente] [varchar](max) NULL,
	[Monto Deuda] [float] NULL,
	[Cliente DNI/CUIT] [varchar](max) NULL,
	[Plan] [varchar](max) NULL,
	[Linea Porta Estado] [varchar](max) NULL,
	[Descripcion] [varchar](max) NULL,
	[Operador] [varchar](max) NULL,
	[Fecha Ultima Mod] [date] NULL,
	[Fecha Estimada Distribucion] [date] NULL,
	[Linea Porta Numero] [bigint] NULL,
	[Fecha Porta] [date] NULL,
	[Producto Origen] [varchar](max) NULL,
	[Forma de Entrega] [varchar](max) NULL,
	[Retrabajo] [varchar](max) NULL,
	[Nuevo producto origen] [varchar](max) NULL,
	[Nuevo plan] [varchar](max) NULL,
 CONSTRAINT [PK_Ventas Movilink Alcanzo] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
