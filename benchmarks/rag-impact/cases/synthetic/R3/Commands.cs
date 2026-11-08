namespace Synthetic.R3
{
    public class EmployeeCommand
    {
        public void RestoreEmployeeById(System.Guid id)
        {
            Context.Database.ExecuteSqlCommand("execute sp_UpdateOrgTotalEmployee");
        }

        public void DeleteEmployeeByIds(System.Collections.Generic.List<System.Guid> ids)
        {
            Context.Database.ExecuteSqlCommand("update Employees set Status = -1 where Id in (" + ids + ")");
        }

        public void ReadView()
        {
            Context.Database.SqlQuery<object>("select FullName from EmployeeBaseViews").ToList();
        }

        public void DynamicTable(string tableName)
        {
            Context.Database.ExecuteSqlCommand("update " + tableName + " set Status = -1");
        }
    }
}
