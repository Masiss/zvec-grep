namespace Validation.Billing;

public partial class InvoiceStore
{
    public void LoadInvoice(int id) { }
    public bool TryGet(int id) => true;
    public bool TryGet(string key) => true;
    public void Save(string invoice) { }
}
