-- Schema [RRHH]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'RRHH')
EXEC sys.sp_executesql N'CREATE SCHEMA [RRHH]'
GO
