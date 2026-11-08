namespace Shop.Orders
{
    public class Caller
    {
        public void Run()
        {
            var service = new OrderService();
            service.Update();
        }
    }
}
