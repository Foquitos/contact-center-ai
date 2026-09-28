-- Table [Farmalux].[Normalizador_SalesForce_AsterVoip]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Farmalux].[Normalizador_SalesForce_AsterVoip]') AND type in (N'U'))
BEGIN
CREATE TABLE [Farmalux].[Normalizador_SalesForce_AsterVoip](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[UsuarioSalesForce] [varchar](50) NOT NULL,
	[UsuarioAsterVoip] [varchar](50) NOT NULL,
 CONSTRAINT [PK_Normalizador_SalesForce_AsterVoip] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
