-- Table [dbo].[Odonto_Plus_Clinicas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Odonto_Plus_Clinicas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Odonto_Plus_Clinicas](
	[IdClinica] [int] NOT NULL,
	[clinica] [varchar](max) NOT NULL,
 CONSTRAINT [PK_Odonto_Plus_Clinicas] PRIMARY KEY CLUSTERED 
(
	[IdClinica] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
