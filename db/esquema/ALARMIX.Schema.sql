-- Schema [ALARMIX]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'ALARMIX')
EXEC sys.sp_executesql N'CREATE SCHEMA [ALARMIX]'
GO
