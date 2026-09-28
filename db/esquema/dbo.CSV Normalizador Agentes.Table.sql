-- Table [dbo].[CSV Normalizador Agentes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Normalizador Agentes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Normalizador Agentes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente_ID] [int] NULL,
	[Agente] [varchar](max) NULL,
	[fecha_desde] [datetime] NULL,
	[fecha_hasta] [datetime] NULL,
 CONSTRAINT [PK_CSV Normalizador Agentes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_CSV Normalizador Agentes_fecha_desde]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[CSV Normalizador Agentes] ADD  CONSTRAINT [DF_CSV Normalizador Agentes_fecha_desde]  DEFAULT (getdate()) FOR [fecha_desde]
END
GO
