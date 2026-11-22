namespace Validation.Billing;

public class InvoiceAudit
{
    public void Save(string invoice) { }
    public void Record(string invoice) { Save(invoice); }
}
