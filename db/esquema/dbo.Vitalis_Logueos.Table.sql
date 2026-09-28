-- Table [dbo].[Vitalis_Logueos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis_Logueos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis_Logueos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[id_usuario] [int] NULL,
	[usuario] [varchar](50) NULL,
	[fecha_dia] [date] NULL,
	[inicio_sesion] [datetime] NULL,
	[cierre_sesion] [datetime] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
