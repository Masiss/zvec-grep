namespace Review.Repro;

class Strange
{
    public string Length => "not an integer";
}

class PropertyOverloads
{
    void Target(int value) { }
    void Target(string value) { }
    void Run(Strange strange) { Target(strange.Length); }
}

class NumericOverloads
{
    void Target(int value) { }
    void Target(uint value) { }
    void Run() { Target(2147483648); }
}

class T
{
    public void Touch() { }
}

class Other
{
    public void Touch() { }
}

class ShadowedTypeName
{
    void Run()
    {
        Other T = new Other();
        T.Touch();
    }
}
