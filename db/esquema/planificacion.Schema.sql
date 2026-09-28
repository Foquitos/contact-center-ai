-- Schema [planificacion]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'planificacion')
EXEC sys.sp_executesql N'CREATE SCHEMA [planificacion]'
GO
