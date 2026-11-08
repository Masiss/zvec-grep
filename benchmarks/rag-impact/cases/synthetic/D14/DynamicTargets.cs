using System;
using System.Data.Entity;

namespace Synthetic.Dynamic
{
    public class DynamicTargets
    {
        private readonly DbContext Context;

        public void Run(string methodName, string tableName)
        {
            var type = Type.GetType("Synthetic.Dynamic.Handlers");
            var method = type.GetMethod(methodName);
            method.Invoke(null, null);

            Context.Database.ExecuteSqlCommand("UPDATE " + tableName + " SET Status = 1");
        }
    }
}
