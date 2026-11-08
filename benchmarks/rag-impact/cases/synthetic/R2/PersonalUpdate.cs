namespace Synthetic.Personal
{
    public class Employee
    {
        public string FullName { get; set; }
        public string Email { get; set; }
        public int TrangThaiCongViec { get; set; }
        public string Keyword { get; set; }
    }

    public class EmployeeDetail
    {
        public string MaChamCong { get; set; }
    }

    public class EmployeeEditModel
    {
        public System.Guid Id { get; set; }
        public string FullName { get; set; }
        public string Email { get; set; }
        public int TrangThaiCongViec { get; set; }
        public string MaChamCong { get; set; }
    }

    public class CoreDBContext
    {
        public DbSet<Employee> Employees { get; set; }
        public DbSet<EmployeeDetail> EmployeeDetails { get; set; }
        public void SaveChanges() { }
    }

    public class EmployeeCommand
    {
        public CoreDBContext Context { get; set; }

        public object EmployeeEditThongTinCaNhan(EmployeeEditModel model)
        {
            var u = Context.Employees.FirstOrDefault(x => x.Id == model.Id);
            if (u == null)
                return null;

            var changeType = false;
            if (u.TrangThaiCongViec != model.TrangThaiCongViec)
            {
                changeType = true;
            }

            u.FullName = model.FullName;
            u.Email = model.Email;
            u.Keyword = model.FullName;
            Context.SaveChanges();

            var d = Context.EmployeeDetails.FirstOrDefault(x => x.EmployeeId == model.Id);
            if (d == null)
                return null;

            d.MaChamCong = model.MaChamCong;
            Context.SaveChanges();

            if (changeType)
            {
                UpdateOrgTotalEmployee();
            }

            return u;
        }

        public bool UpdateOrgTotalEmployee()
        {
            return true;
        }
    }
}
