-- Table [dbo].[usuarios_con_fecha]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[usuarios_con_fecha]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[usuarios_con_fecha](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[nomina_id] [int] NOT NULL,
	[USUARIO] [varchar](max) NULL,
	[CMS USER] [varchar](max) NULL,
	[USUARIO TERCIARIO] [varchar](max) NULL,
	[USUARIO SECUNDARIO] [varchar](max) NULL,
	[fecha_desde] [datetime] NOT NULL,
	[fecha_hasta] [datetime] NULL,
 CONSTRAINT [PK_usuarios_con_fecha] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_usuarios_con_fecha_fecha_desde]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[usuarios_con_fecha] ADD  CONSTRAINT [DF_usuarios_con_fecha_fecha_desde]  DEFAULT (getdate()) FOR [fecha_desde]
END
GO
