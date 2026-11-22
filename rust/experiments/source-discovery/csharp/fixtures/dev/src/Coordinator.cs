namespace Demo.App;

public class Coordinator
{
    public void Start(CustomerService service, int id)
    {
        service.ProcessCustomer(id);
        service.SaveCustomerAudit(id);
    }

    public void StartUnknown(dynamic service, int id)
    {
        service.SaveCustomerAudit(id);
    }
}
