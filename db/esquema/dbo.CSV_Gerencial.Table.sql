-- Table [dbo].[CSV_Gerencial]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV_Gerencial]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV_Gerencial](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[inicio_intervalo] [datetime] NULL,
	[id_skill] [smallint] NULL,
	[nombre_skill] [varchar](50) NULL,
	[asa] [smallint] NULL,
	[ofrecidas_sin_abn<10] [smallint] NULL,
	[ACD_en_SL] [smallint] NULL,
	[LL_ACD] [smallint] NULL,
	[ABN_en_SL_+_Discalls] [smallint] NULL,
	[total_ACD] [int] NULL,
 CONSTRAINT [PK_CSV_Gerencial] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
