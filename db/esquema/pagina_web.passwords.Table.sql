-- Table [pagina_web].[passwords]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[passwords]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[passwords](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[nomina_id] [int] NOT NULL,
	[password] [char](60) NULL,
	[must_change_password] [bit] NOT NULL,
 CONSTRAINT [PK_passwords] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_passwords_must_change_password]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[passwords] ADD  CONSTRAINT [DF_passwords_must_change_password]  DEFAULT ((1)) FOR [must_change_password]
END
GO
