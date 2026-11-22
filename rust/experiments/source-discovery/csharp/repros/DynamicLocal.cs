namespace Review.Repro;

class DynamicCase
{
    void Target(string value) { }

    void Caller()
    {
        dynamic value = "x";
        Target(value);
    }
}
