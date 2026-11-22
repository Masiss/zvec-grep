namespace Review.Repro;

class LocalFunctionCase
{
    void Target() { }

    void Caller()
    {
        void Target() { }
        Target();
    }
}
