namespace Validation.Billing;

public partial class InvoiceStore
{
    public void BuildInvoice(int id)
    {
        LoadInvoice(id);
        TryGet(id);
        Save("invoice");
    }

    public void BuildInvoice(string key)
    {
        TryGet(key);
        Save(key);
    }

    public void BuildInvoiceUnknown(dynamic value)
    {
        TryGet(value);
    }
}
