-- Table [dbo].[Voltara approach ranking total conexiones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara approach ranking total conexiones]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara approach ranking total conexiones](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[USERID] [int] NULL,
	[AGENTID] [varchar](max) NULL,
	[CSESSION] [time](7) NULL,
	[PCTDELAY] [int] NULL,
	[CTALKING_I] [time](7) NULL,
	[CTALKING_O] [time](7) NULL,
	[CTALKING] [time](7) NULL,
	[PCTTALKING] [int] NULL,
	[CACW] [time](7) NULL,
	[PCTACW] [int] NULL,
	[CALLS_I] [int] NULL,
	[CALLS_O] [int] NULL,
	[CALLS] [int] NULL,
	[CONEXAVG_I] [time](7) NULL,
	[CONEXAVG_O] [time](7) NULL,
	[CONEXAVG] [time](7) NULL,
	[CWAITING] [time](7) NULL,
	[PCTWAITING] [float] NULL,
	[PCTABC] [float] NULL,
	[CDND] [varchar](max) NULL,
	[PCTDND] [float] NULL,
	[CDELAY] [time](7) NULL,
	[XFER] [int] NULL,
	[XFERED] [int] NULL,
	[THRCOUNT] [int] NULL,
	[CTHRTALKING] [time](7) NULL,
	[CHOLD] [time](7) NULL,
	[CCNSTIME] [time](7) NULL,
	[CNSNUM] [int] NULL,
	[CCNSEDTIME] [time](7) NULL,
	[CNSEDNUM] [int] NULL,
	[CCNSNOHTIME] [varchar](max) NULL,
	[CNSNOHNUM] [int] NULL,
	[ALOGIN] [int] NULL,
	[TOTLOGIN] [int] NULL,
	[NOANSWER] [int] NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Voltara approach ranking total conexiones] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
