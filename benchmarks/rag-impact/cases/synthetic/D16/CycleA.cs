namespace Synthetic.Cycle
{
    public class CycleA
    {
        public void A()
        {
            new CycleB().B();
        }
    }
}
