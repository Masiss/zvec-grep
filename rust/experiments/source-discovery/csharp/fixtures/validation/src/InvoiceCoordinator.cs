namespace Validation.Billing;

public class InvoiceCoordinator
{
    public void Open(InvoiceStore store, int id)
    {
        store.LoadInvoice(id);
        store.Save("invoice");
    }
}
