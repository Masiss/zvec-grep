CREATE PROCEDURE [dbo].[sp_UpdateOrgTotalEmployee_old]
AS
BEGIN
    UPDATE Organizations SET TotalEmployee = 0
END
