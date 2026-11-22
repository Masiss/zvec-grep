namespace Review.Repro;

class Box<T>
{
    void Ping() { }
    void Caller() { Ping(); }
}

class Box<T, U>
{
    void Ping() { }
    void Caller() { Ping(); }
}
