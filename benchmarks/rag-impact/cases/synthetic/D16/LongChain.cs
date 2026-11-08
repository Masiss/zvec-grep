namespace Synthetic.Cycle
{
    public class LongChain
    {
        public void M0() { M1(); }
        public void M1() { M2(); }
        public void M2() { M3(); }
        public void M3() { M4(); }
        public void M4() { M5(); }
        public void M5() { M6(); }
        public void M6() { M7(); }
        public void M7() { M8(); }
        public void M8() { M9(); }
        public void M9() { M10(); }
        public void M10() { M11(); }
        public void M11() { M12(); }
        public void M12() { }
    }
}
