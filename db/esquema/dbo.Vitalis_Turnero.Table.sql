-- Table [dbo].[Vitalis_Turnero]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis_Turnero]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis_Turnero](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Usuario] [varchar](50) NULL,
	[Cantidad de turnos] [int] NULL,
	[Canal] [varchar](20) NULL,
 CONSTRAINT [PK_Vitalis_Turnero] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
