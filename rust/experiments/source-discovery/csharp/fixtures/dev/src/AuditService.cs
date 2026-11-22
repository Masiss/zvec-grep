namespace Demo.App;

public class AuditService
{
    public void SaveCustomerAudit(int id) { }
    public void ProcessAudit(int id) { SaveCustomerAudit(id); }
}
