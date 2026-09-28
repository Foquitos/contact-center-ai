-- Schema [CSV]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'CSV')
EXEC sys.sp_executesql N'CREATE SCHEMA [CSV]'
GO
