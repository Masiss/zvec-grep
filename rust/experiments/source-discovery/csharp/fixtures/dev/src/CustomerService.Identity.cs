namespace Demo.App;

public partial class CustomerService
{
    public void NormalizeCustomerName(string name) { }

    public void ProcessName(string name)
    {
        NormalizeCustomerName(name);
        RefreshCustomer(name);
    }

    public void Parse(int value) { }
    public void Parse(string value) { }
    public void Parse(object value) { }

    public void ParseKnown(int value) { Parse(value); }
    public void ParseStringKnown(string value) { Parse(value); }
    public void ParseUnknown(dynamic value) { Parse(value); }
}
