namespace Review.Repro;

class GenericMethods
{
    void Parse() { }
    void Parse<T>() { }
    void Run()
    {
        Parse();
        Parse<int>();
    }
}
