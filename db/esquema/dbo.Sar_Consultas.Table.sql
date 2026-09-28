-- Table [dbo].[Sar_Consultas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Sar_Consultas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Sar_Consultas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Numero] [varchar](max) NULL,
	[Fecha de Ingreso] [datetime] NULL,
	[Telefono] [varchar](max) NULL,
 CONSTRAINT [PK_Sar_Consultas] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
