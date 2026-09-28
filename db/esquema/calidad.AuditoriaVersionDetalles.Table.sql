-- Table [calidad].[AuditoriaVersionDetalles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AuditoriaVersionDetalles]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AuditoriaVersionDetalles](
	[VersionAuditoriaID] [int] NOT NULL,
	[AtributoID] [int] NOT NULL,
	[ValorResultado] [nvarchar](max) NULL,
	[Orden] [int] NULL,
	[PonderacionAplicada] [float] NULL,
 CONSTRAINT [PK_AuditoriaVersionDetalles] PRIMARY KEY CLUSTERED 
(
	[VersionAuditoriaID] ASC,
	[AtributoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaVersionDetalles_Version]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaVersionDetalles]'))
ALTER TABLE [calidad].[AuditoriaVersionDetalles]  WITH CHECK ADD  CONSTRAINT [FK_AuditoriaVersionDetalles_Version] FOREIGN KEY([VersionAuditoriaID])
REFERENCES [calidad].[AuditoriaVersiones] ([VersionAuditoriaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_AuditoriaVersionDetalles_Version]') AND parent_object_id = OBJECT_ID(N'[calidad].[AuditoriaVersionDetalles]'))
ALTER TABLE [calidad].[AuditoriaVersionDetalles] CHECK CONSTRAINT [FK_AuditoriaVersionDetalles_Version]
GO
