-- Schema [orion]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'orion')
EXEC sys.sp_executesql N'CREATE SCHEMA [orion]'
GO
