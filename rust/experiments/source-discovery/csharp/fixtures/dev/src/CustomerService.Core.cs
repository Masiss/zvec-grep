namespace Demo.App;

public partial class CustomerService
{
    public void RefreshCustomer(int id) { }
    public void RefreshCustomer(string id) { }
    public void SaveCustomerAudit(int id) { }

    public void ProcessCustomer(int id)
    {
        RefreshCustomer(id);
        SaveCustomerAudit(id);
    }

    public void ProcessCustomer(string id)
    {
        RefreshCustomer(id);
    }

    public void ProcessCustomer(int id, bool writeAudit)
    {
        RefreshCustomer(id);
        if (writeAudit) SaveCustomerAudit(id);
    }

    public void HasLexicalNoise()
    {
        // NeverCalled() and RefreshCustomer("comment") are not references.
        var example = "RefreshCustomer(id); NeverCalled();";
        _ = example;
    }
}
