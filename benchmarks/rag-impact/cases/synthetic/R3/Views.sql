CREATE VIEW [dbo].[EmployeeBaseViews]
AS
SELECT e.Id, e.StaffCode, e.FullName, o.Name AS OrganizationName
FROM dbo.Employees AS e
LEFT JOIN dbo.Organizations AS o ON o.Id = e.LastOrgId
WHERE e.Status >= 0
