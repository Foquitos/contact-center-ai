-- Table [dbo].[campanas_empresa_mitrol]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[campanas_empresa_mitrol]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[campanas_empresa_mitrol](
	[idCampania] [int] NOT NULL,
	[Campaña] [nvarchar](max) NULL,
	[Empresa] [nvarchar](max) NULL,
	[id_interno] [int] IDENTITY(1,1) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[id_interno] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
