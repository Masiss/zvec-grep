CREATE PROCEDURE [dbo].[sp_UpdateOrgTotalEmployee]
AS
BEGIN
    UPDATE Organizations
    SET TotalEmployee = (
        SELECT COUNT(DISTINCT eo.EmployeeId)
        FROM EmployeeOrganizations eo
        JOIN Employees e ON e.Id = eo.EmployeeId
        WHERE e.Status >= 0
    )
    WHERE Id = 1
END
