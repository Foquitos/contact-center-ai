-- Table [calidad].[Batch_data]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Batch_data]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Batch_data](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Batch_id] [varchar](50) NOT NULL,
	[segment_id] [int] NOT NULL,
	[columna] [varchar](50) NOT NULL,
	[valor] [varchar](max) NULL,
	[fecha] [datetime] NOT NULL,
 CONSTRAINT [PK_Batch_data] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_Batch_data_fecha]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Batch_data] ADD  CONSTRAINT [DF_Batch_data_fecha]  DEFAULT (getdate()) FOR [fecha]
END
GO
