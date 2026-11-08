namespace Synthetic.Cycle
{
    public class CycleB
    {
        public void B()
        {
            new CycleA().A();
        }
    }
}
