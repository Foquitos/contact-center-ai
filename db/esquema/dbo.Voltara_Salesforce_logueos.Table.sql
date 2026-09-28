-- Table [dbo].[Voltara_Salesforce_logueos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_Salesforce_logueos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_Salesforce_logueos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[ESTADO] [varchar](max) NULL,
	[INICIO] [smalldatetime] NULL,
	[FIN] [smalldatetime] NULL,
	[DURACIÓN] [int] NULL,
	[AUSENTE] [bit] NULL,
	[Usuario] [varchar](max) NULL,
	[Fecha Inicio]  AS (CONVERT([date],[INICIO])),
 CONSTRAINT [PK_Voltara_Salesforce_logueos] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
