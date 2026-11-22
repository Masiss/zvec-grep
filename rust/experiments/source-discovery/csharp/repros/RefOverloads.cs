namespace Review.Repro;

class RefOverloads
{
    void Read(int value) { }
    void Read(ref int value) { }

    void Run()
    {
        int value = 0;
        Read(value);
        Read(ref value);
    }
}
