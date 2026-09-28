-- Schema [Mitrol]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'Mitrol')
EXEC sys.sp_executesql N'CREATE SCHEMA [Mitrol]'
GO
