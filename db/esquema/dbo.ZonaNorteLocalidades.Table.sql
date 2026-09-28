-- Table [dbo].[ZonaNorteLocalidades]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ZonaNorteLocalidades]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[ZonaNorteLocalidades](
	[Localidad] [nvarchar](100) NULL,
	[Zona] [nvarchar](50) NULL
) ON [PRIMARY]
END
GO
