-- Table [dbo].[equipos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[equipos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[equipos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[nombre] [varchar](50) NULL,
	[apellido] [varchar](50) NULL,
	[campana_id] [int] NULL,
	[jefatura] [int] NULL,
	[id_nomina] [int] NULL,
	[fecha_desde] [datetime] NULL,
	[fecha_hasta] [datetime] NULL,
 CONSTRAINT [PK__equipos__3213E83FBEB33AD4] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF__equipos__fecha_d__668030F6]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[equipos] ADD  CONSTRAINT [DF__equipos__fecha_d__668030F6]  DEFAULT (getdate()) FOR [fecha_desde]
END
GO
