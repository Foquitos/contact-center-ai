-- Table [dbo].[Instagram_Perfil]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Instagram_Perfil]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Instagram_Perfil](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Seguidores] [int] NULL,
	[Seguidos] [smallint] NULL,
 CONSTRAINT [PK_Instagram_Perfil] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
